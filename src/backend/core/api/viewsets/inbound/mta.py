"""MTA channel implementation for handling email delivery."""

import email.utils
import hashlib
import ipaddress
import json
import logging
import re
import secrets

from django.conf import settings
from django.utils import timezone

import jwt
from drf_spectacular.utils import extend_schema
from jmap_email import parse_email
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import enums, models
from core.mda.inbound import (
    DeliveryUnavailable,
    check_local_recipients,
    deliver_inbound_message,
)
from core.mda.raw_mime import remove_mime_headers

logger = logging.getLogger(__name__)

# ``mta_session`` is mta-in's per-connection correlation id
# (``secrets.token_hex(4)``), carried as a JWT claim so its SMTP-side lines and
# ours can be joined. Named for its origin: a bare ``session`` would read as a
# Django session here.
# Matched with ``fullmatch``: ``$`` also matches just before a trailing
# newline, so an anchored ``match`` would accept ``"deadbeef\n"`` — the exact
# shape the check exists to keep out of a log record.
_MTA_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


# Content-Type of a deliver request that carries the SMTP envelope in its body
# rather than in the JWT claims. See ``InboundMTAViewSet.deliver``.
ENVELOPED_CONTENT_TYPE = "application/vnd.stmsg.mta-delivery"


# Longest envelope line read from an enveloped deliver body. An envelope of
# 100 recipients is about 35 kB; this leaves room for a few thousand.
_MAX_ENVELOPE_LINE = 1024 * 1024


def _split_enveloped_body(body: bytes) -> tuple[dict, bytes] | None:
    """Split an enveloped deliver body into (envelope, message).

    The envelope is everything up to the first newline, as a JSON object. JSON
    on one line holds no raw newline, so the first one in the body is the
    separator whatever the message contains.

    Returns None when the body does not start with such a line: no newline
    within ``_MAX_ENVELOPE_LINE`` bytes, or a line that is not a JSON object.
    """
    end = body.find(b"\n", 0, _MAX_ENVELOPE_LINE)
    if end < 0:
        return None
    try:
        envelope = json.loads(body[:end])
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    return envelope, body[end + 1 :]


# A protocol keyword, a TLS version, a cipher suite or a host name of ours, as
# they may appear in a trace header: nothing that could end or restructure it.
_TRACE_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
# What a client may call itself in HELO, kept to characters of a host name or
# an address literal. Anything else is shown as "?".
_HELO_UNSAFE_RE = re.compile(r"[^\w.\-:\[\]]")


def _trace_token(metadata: dict, key: str) -> str:
    value = metadata.get(key)
    if isinstance(value, str) and _TRACE_TOKEN_RE.fullmatch(value):
        return value
    return ""


def _received_header(metadata: dict, now) -> str:
    """The ``Received`` header for the hop from the sending host into our MTA.

    Always written, by us, on top of what the sender sent. It is the only
    lasting record of the client address (the envelope goes with the queue
    row), and it is what bounds the headers the spam rules and
    ``Authentication-Results`` reader may trust: ``headers_blocks`` trusts
    what is above the first ``Received``, which without this line was whatever
    the sender put there.

    Format, RFC 5321 section 4.4::

        Received: from <helo> ([<ip>])
            by <our MTA> with <protocol> id <session> tls <cipher> (<version>);
            <date>

    - ``with`` is ESMTP or ESMTPS (RFC 3848), UTF8SMTP or UTF8SMTPS
      (RFC 6531): the S says the session was encrypted.
    - ``tls`` names the cipher suite (RFC 8314 section 4.3). The protocol
      version has no clause of its own and goes in a comment, as Postfix
      writes it.
    - No ``for`` clause: it would differ per recipient, split the shared
      blob, and disclose Bcc recipients to each other.

    Each field is checked here: the calling MTA validates them too, but this
    line ends up in every stored message.
    """
    helo = metadata.get("client_helo")
    helo = (
        _HELO_UNSAFE_RE.sub("?", helo)[:255]
        if isinstance(helo, str) and helo
        else "unknown"
    )

    tcp_info = []
    hostname = metadata.get("client_hostname")
    if isinstance(hostname, str) and hostname:
        tcp_info.append(_HELO_UNSAFE_RE.sub("?", hostname)[:255])
    try:
        address = ipaddress.ip_address(str(metadata.get("client_address")))
    except ValueError:
        address = None
    if address is not None:
        tcp_info.append(f"[IPv6:{address}]" if address.version == 6 else f"[{address}]")
    sent_from = f"from {helo}" + (f" ({' '.join(tcp_info)})" if tcp_info else "")

    received_by = []
    if server_name := _trace_token(metadata, "server_name"):
        received_by.append(f"by {server_name}")
    if protocol := _trace_token(metadata, "protocol"):
        received_by.append(f"with {protocol}")
    if (session := _mta_session(metadata)) != "-":
        received_by.append(f"id {session}")
    if cipher := _trace_token(metadata, "tls_cipher"):
        received_by.append(f"tls {cipher}")
    if version := _trace_token(metadata, "tls_version"):
        received_by.append(f"({version})")

    lines = [sent_from] + ([" ".join(received_by)] if received_by else [])
    date = email.utils.format_datetime(now)
    return "Received: " + "\r\n\t".join(lines) + f";\r\n\t{date}\r\n"


def _is_recipient_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _mta_session(mta_metadata) -> str:
    """The correlation id from *mta_metadata*, or ``"-"`` if unusable.

    Shape-checked before it reaches a log line: our logging does no escaping,
    so a value carrying a newline would forge records. The claim is signed, so
    this only matters if the shared secret leaks — but that is exactly when
    trustworthy logs matter most. Absent for an older mta-in, hence a
    placeholder rather than a refusal.
    """
    value = (mta_metadata or {}).get("mta_session")
    return value if isinstance(value, str) and _MTA_SESSION_RE.fullmatch(value) else "-"


class MTAJWTAuthentication(BaseAuthentication):
    """Authenticate the MTA-to-MDA channel via an HS256 JWT.

    Trust model: the whole channel rests on the shared ``MDA_API_SECRET``.
    Only the MTA-in service knows it, so a valid HMAC signature *is* the proof
    of identity — there is no per-request identity beyond "signed by the
    secret". Consequently we do NOT attempt replay protection (a ``jti`` nonce
    store, etc.): anyone able to forge the signature already holds the secret
    and could mint fresh tokens at will, and anyone who cannot is stopped by
    the signature check. Keeping the secret out of source (it has no default —
    see ``settings.MDA_API_SECRET``) and the transport on TLS is what actually
    secures this path.

    On top of the signature we keep two cheap, narrow guards:
    - ``exp``: bounds a leaked token's useful lifetime. The issuer sizes the
      claim to cover its full retry window (see mta-in ``mda_api_call``).
    - ``body_hash``: binds the token to its exact request body, so a captured
      token can't be repurposed for a *different* body within that window.
      Enforced even for an empty body (the bodyless ``/check`` path).

    There is a single secret with no overlap window: during a rotation, the
    MDA and the MTA-in refuse each other's tokens until both run with the new
    value.

    Returns None or (user, auth).
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        if not settings.MDA_API_SECRET:
            # PyJWT raises TypeError / InvalidKeyError on an empty key, which
            # would surface as a 500.
            logger.error("MDA_API_SECRET is not configured, refusing MTA request")
            raise AuthenticationFailed("Invalid token")

        try:
            jwt_token = auth_header.split(" ")[1]
            payload = jwt.decode(
                jwt_token,
                settings.MDA_API_SECRET,
                algorithms=["HS256"],
                options={
                    # exp bounds the lifetime; body_hash binds the token to its
                    # payload. Both are mandatory.
                    "require": ["exp", "body_hash"],
                    "verify_exp": True,
                    "verify_signature": True,
                },
            )

            # Bind the token to its payload. Always enforced — including for
            # an empty body (sha256 of b"") — so the bodyless /check endpoint
            # can't be driven with a token minted for a different request.
            claimed_hash = payload["body_hash"]
            # ``compare_digest`` raises TypeError on mismatched types (e.g. a
            # numeric ``body_hash`` claim), which would surface as a 500 rather
            # than an auth failure. Reject a non-string claim up front.
            if not isinstance(claimed_hash, str):
                raise jwt.InvalidTokenError("Invalid email hash")
            body_hash = hashlib.sha256(request.body or b"").hexdigest()
            if not secrets.compare_digest(body_hash, claimed_hash):
                raise jwt.InvalidTokenError("Invalid email hash")

            service_account = models.User()
            return (service_account, payload)

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            raise AuthenticationFailed("Invalid token") from e
        except (IndexError, KeyError) as e:
            raise AuthenticationFailed("Invalid token header or payload") from e

    def authenticate_header(self, request):
        """Return the header to be used in the WWW-Authenticate response header."""
        return 'Bearer realm="MTA"'


class InboundMTAViewSet(viewsets.GenericViewSet):
    """Handles incoming email messages from MTA (Mail Transfer Agent)."""

    # Channel metadata
    CHANNEL_TYPE = "mta"
    CHANNEL_DESCRIPTION = "Mail Transfer Agent (email)"

    permission_classes = [IsAuthenticated]
    authentication_classes = [MTAJWTAuthentication]

    @extend_schema(exclude=True)
    @action(
        detail=False, methods=["post"], url_path="check", url_name="inbound-mta-check"
    )
    def check(self, request):
        """Check recipients exist."""
        data = request.data
        addresses = data.get("addresses", [])
        if not addresses or not isinstance(addresses, list):
            return Response(
                {"detail": "Missing addresses"}, status=status.HTTP_400_BAD_REQUEST
            )

        local_addresses = check_local_recipients(addresses)
        results = {address: address in local_addresses for address in addresses}
        return Response(results)

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="deliver",
        url_name="inbound-mta-deliver",
    )
    def deliver(self, request):
        """Handle an incoming raw email from the MTA.

        Two request forms, told apart by Content-Type:

        - ``message/rfc822``: the body is the message, and the SMTP envelope
          (sender, recipients, client address…) rides in the JWT claims. An
          HTTP header caps what an envelope can hold (8190 bytes on gunicorn),
          which about 100 recipients exceed. Kept for the Postfix milter and
          for an mta-in older than this endpoint.
        - ``ENVELOPED_CONTENT_TYPE``: the body is the envelope as one line of
          JSON, a newline, then the message. The token's ``body_hash`` covers
          the whole body, so the envelope is authenticated exactly as the
          claims were.
        """

        # request.user will be the service account, request.auth the JWT payload
        if request.content_type == "message/rfc822":
            mta_metadata = request.auth
            raw_data = request.body
        elif request.content_type == ENVELOPED_CONTENT_TYPE:
            # A body that does not split is answered below, with the 500 that
            # missing metadata gets.
            mta_metadata, raw_data = _split_enveloped_body(request.body) or (None, b"")
            if mta_metadata is not None:
                # The correlation id stays a claim in both forms.
                mta_metadata["mta_session"] = (request.auth or {}).get("mta_session")
        else:
            logger.warning(
                "Received inbound POST with incorrect Content-Type: %s",
                request.content_type,
            )
            return Response(
                {
                    "detail": "Content-Type must be message/rfc822 or "
                    + ENVELOPED_CONTENT_TYPE
                },
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        if not mta_metadata or not _is_recipient_list(
            mta_metadata.get("original_recipients")
        ):
            # Ours to fix, not a verdict on the message: a 5xx makes the MTA
            # defer instead of bouncing the mail.
            logger.error("MTA metadata missing or malformed in authenticated request.")
            return Response(
                {"detail": "Internal authentication error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not raw_data:
            logger.error("Received empty body for inbound email.")
            return Response(
                {"status": "error", "detail": "Empty request body"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate incoming email size
        email_size = len(raw_data)
        if email_size > settings.MAX_INCOMING_EMAIL_SIZE:
            logger.warning(
                "Incoming email size (%d bytes) exceeds maximum allowed size (%d bytes)",
                email_size,
                settings.MAX_INCOMING_EMAIL_SIZE,
            )
            return Response(
                {
                    "status": "error",
                    "detail": f"Incoming email size ({email_size} bytes) exceeds maximum allowed size "
                    + f"({settings.MAX_INCOMING_EMAIL_SIZE} bytes)",
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        logger.info(
            "Raw email received: %d bytes for %s [mta_session=%s]",
            len(raw_data),
            mta_metadata["original_recipients"],  # Log all intended recipients
            _mta_session(mta_metadata),
        )

        # Strip sender-supplied headers we own or authoritatively rewrite:
        #  - ``X-StMsg-*``: our pipeline namespace (sender-auth etc.) — must not
        #    be forgeable.
        #  - ``Return-Path``: only the delivering MTA may write it (RFC 5321
        #    §4.4); we rewrite it below from the real envelope MAIL FROM, so any
        #    inbound copy is forged and must go first.
        raw_data = remove_mime_headers(
            raw_data, prefixes=["x-stmsg-"], names=["return-path"]
        )

        def sanitize_header(header: str) -> str:
            # NUL and TAB alongside CR/LF: pymta forbids all four in these
            # fields, but the backend cannot assume the calling MTA validated
            # them, and a NUL would land literally in a stored header.
            for char in ("\r", "\n", "\x00", "\t"):
                header = header.replace(char, "")
            return header[0:255]

        # Bake the immutable envelope facts as standard headers at ingest, on
        # top of the received bytes and BEFORE the blob is created, so they ride
        # in the single stored blob:
        #  - ``Return-Path``: the envelope MAIL FROM (``<>`` for a null sender /
        #    bounce). Durable home for what the autoreply/bounce logic reads.
        #  - ``Received``: the trace line for the hop into our MTA, always
        #    written (see ``_received_header``). Shared across recipients (one
        #    prepend, no ``for`` clause), so one delivery is still one blob.
        sender = mta_metadata.get("sender") or ""
        client_helo = mta_metadata.get("client_helo")
        client_hostname = mta_metadata.get("client_hostname")
        client_address = mta_metadata.get("client_address")

        # Return-Path first, then Received: everything the sender supplied
        # stays below our own trace line.
        # Cut inside the brackets, so a long sender keeps the closing one:
        # 256 octets with them (RFC 5321 section 4.5.3.1.3).
        return_path = f"<{sanitize_header(sender)[:254]}>" if sender else "<>"
        raw_data = (
            f"Return-Path: {return_path}\r\n"
            + _received_header(mta_metadata, timezone.now())
        ).encode("utf-8") + raw_data

        # Parse the email message once
        parsed_email = parse_email(raw_data)
        if parsed_email is None:
            # Sender-supplied malformed input; not an internal error.
            logger.warning("Failed to parse inbound email (returning 400)")
            # Consider saving the raw email for debugging
            return Response(
                {"status": "error", "detail": "Failed to parse email"},
                status=status.HTTP_400_BAD_REQUEST,  # Bad request as email is malformed
            )

        # Structured SMTP envelope carried alongside the bytes while the
        # message is queued, for the pipeline (rspamd, autoreply). ``sender``
        # is the MAIL FROM; the client_* fields are the connecting SMTP peer
        # and the TLS ones how it connected (forwarded by the MTA, empty for a
        # session in clear). ``rcpt_to`` is the actual RCPT TO: per recipient,
        # so set inside the loop and kept out of the shared blob.
        base_envelope = {
            "origin": enums.InboundOrigin.MTA,
            "mail_from": sender,
            "ip": client_address or "",
            "helo": client_helo or "",
            "hostname": client_hostname or "",
        }
        # Only from an MTA that reports how the session went (it sends
        # ``protocol``): an absent key means "not known", which is not the
        # same as an empty one, "in clear".
        if "protocol" in mta_metadata:
            base_envelope["tls_version"] = _trace_token(mta_metadata, "tls_version")
            base_envelope["tls_cipher"] = _trace_token(mta_metadata, "tls_cipher")

        # Deliver the parsed email to each original recipient
        success_count = 0
        failure_count = 0
        unavailable = False
        delivery_results = {}

        for recipient in mta_metadata["original_recipients"]:
            try:
                delivered = deliver_inbound_message(
                    recipient,
                    parsed_email,
                    raw_data,
                    envelope={**base_envelope, "rcpt_to": recipient},
                    raise_unavailable=True,
                )
                if delivered:
                    success_count += 1
                    delivery_results[recipient] = "Success"
                else:
                    # Delivery function failed (and logged the reason)
                    failure_count += 1
                    delivery_results[recipient] = "Failed"
            except DeliveryUnavailable:
                unavailable = True
                failure_count += 1
                delivery_results[recipient] = "Unavailable"
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Unexpected error during delivery loop for %s: %s",
                    recipient,
                    e,
                    exc_info=True,
                )
                failure_count += 1
                delivery_results[recipient] = f"Error: {e}"

        # Determine overall status based on counts
        if failure_count > 0 and success_count == 0:
            logger.error(
                "All deliveries failed for inbound email. [mta_session=%s]",
                _mta_session(mta_metadata),
            )
            if unavailable:
                # Without "status": "error", so the MTA counts it toward its
                # circuit breaker. The 500 below it does not: a sender whose
                # messages fail must not open the breaker for everyone.
                return Response(
                    {
                        "status": "unavailable",
                        "detail": "Delivery is temporarily unavailable",
                        "results": delivery_results,
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {
                    "status": "error",
                    "detail": "Failed to deliver message to any recipient",
                    "results": delivery_results,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if failure_count > 0:
            # If some deliveries failed, return 207 Multi-Status
            logger.warning(
                "Partial delivery failure: %d successful, %d failed [mta_session=%s]",
                success_count,
                failure_count,
                _mta_session(mta_metadata),
            )
            return Response(
                {
                    "status": "partial_success",
                    "delivered": success_count,
                    "failed": failure_count,
                    "results": delivery_results,
                },
                status=status.HTTP_207_MULTI_STATUS,
            )

        # All deliveries successful
        logger.info(
            "All %d deliveries successful for inbound email. [mta_session=%s]",
            success_count,
            _mta_session(mta_metadata),
        )
        return Response({"status": "ok", "delivered": success_count})

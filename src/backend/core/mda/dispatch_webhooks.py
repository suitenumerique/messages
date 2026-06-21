"""User-configured outbound webhooks, modelled as pipeline ``Step``s.

For each delivered message the inbound pipeline (see
``inbound_pipeline.py``) iterates every webhook-type Channel that
matches the destination mailbox (``scope_level=MAILBOX``), its domain
(``scope_level=MAILDOMAIN``), or any global channel
(``scope_level=GLOBAL``). The webhook fires either ``before_spam`` or
``after_spam`` according to ``settings.phase``; a ``blocking`` webhook
can abort delivery (DROP), request retry (RETRY), override the spam
verdict, or attach labels via its JSON response body.

This file is webhook-specific: HTTP plumbing, signing (HMAC + JWT or
API key), JMAP body building, SSRF-safe POST, response classification.
The pipeline-side glue is ``UserWebhookStep`` + ``webhook_steps_for_mailbox``.

The HTTP client is the shared ``SSRFSafeSession`` — webhook URLs are
attacker-controllable, so the same hostname/IP rejection rules used by
the image proxy and IMAP importer apply here too.

Two body formats are supported (see ``docs/webhooks.md``):
  - ``format="eml"`` (default): raw RFC-822 bytes, ``Content-Type:
    message/rfc822``. Webhook envelope metadata lives in ``X-StMsg-*``
    headers.
  - ``format="jmap"``: JMAP-compliant ``Email`` object (RFC 8621 §4.1)
    serialised as a single JSON document with ``Content-Type:
    application/json``. The body is a strictly compliant Email object —
    same envelope metadata in ``X-StMsg-*`` headers.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from django.db.models import Q

import jwt
from jmap_email import JmapEmail

from core import enums, models
from core.mda.inbound_pipeline import Decision, InboundContext, Step
from core.services.ssrf import SSRFSafeSession, SSRFValidationError

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 30  # seconds

# Hard cap on the receiver response body we parse for the action JSON.
# The contract body is tiny (action / is_spam / labels = a few hundred
# bytes at most). A bigger response is almost certainly an HTML error
# page from a misconfigured proxy — parse what we have, ignore the
# rest, never let a misbehaving receiver OOM the worker.
MAX_RESPONSE_BODY = 64 * 1024  # 64 KiB

# Per-action input caps. A receiver can't make us do unbounded work
# from one webhook call: extra entries past the cap are silently
# dropped at parse time.
MAX_LABELS_PER_RESPONSE = 50
MAX_ASSIGN_TO_PER_RESPONSE = 50
MAX_EVENTS_PER_RESPONSE = 20
MAX_IM_CONTENT_BYTES = 8 * 1024  # 8 KiB per internal-message comment

PHASE_BEFORE_SPAM = "before_spam"
PHASE_AFTER_SPAM = "after_spam"
VALID_PHASES = frozenset({PHASE_BEFORE_SPAM, PHASE_AFTER_SPAM})


@dataclass
class _HttpResult:  # pylint: disable=too-many-instance-attributes
    """Internal: one webhook call's outcome — decision + the side
    effects the receiver asked us to apply to the pipeline context.

    The ``UserWebhookStep`` applies these to its ``InboundContext``
    and returns the decision; outside this file the type is invisible.

    Bool flag fields (``mark_starred`` / ``mark_read`` / ``mark_trashed``
    / ``mark_archived`` / ``skip_autoreply``) follow ``true``-only
    semantics: a receiver returning ``true`` opts in; anything else
    (``false``, missing, non-bool) is "no opinion". This makes the
    multi-webhook merge a simple OR so a later receiver can't
    accidentally veto an earlier receiver's directive.
    """

    decision: Decision = Decision.CONTINUE
    is_spam_override: Optional[bool] = None
    labels: Set[str] = field(default_factory=set)
    # Ordered, lowercased, deduped — preserves the receiver's intent
    # while letting the pipeline use cheap set/list operations downstream.
    assign_to: List[str] = field(default_factory=list)
    mark_starred: bool = False
    mark_read: bool = False
    mark_trashed: bool = False
    mark_archived: bool = False
    skip_autoreply: bool = False
    # add_event: each entry is a validated dict ready to be persisted
    # as a ThreadEvent. Currently only ``type=im`` is supported.
    events: List[Dict[str, Any]] = field(default_factory=list)
    # reply_draft: receiver-supplied MessageTemplate UUID. Resolved +
    # scope-checked + drafted in the pipeline finalize step.
    reply_draft_template_id: Optional[str] = None


def _read_capped_body(response) -> bytes:
    """Read at most ``MAX_RESPONSE_BODY`` bytes from a streaming response.

    The action body contract is tiny (a few hundred bytes). Reading
    more is wasted memory at best and a DoS vector at worst — if a
    receiver returns a huge payload we keep what we have and ignore
    the rest. Network errors mid-stream get logged and the caller
    treats the partial body as if the receiver had returned no body.
    """
    chunks: List[bytes] = []
    received = 0
    try:
        for chunk in response.iter_content(chunk_size=8192, decode_unicode=False):
            if not chunk:
                continue
            remaining = MAX_RESPONSE_BODY - received
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                received = MAX_RESPONSE_BODY
                break
            chunks.append(chunk)
            received += len(chunk)
    except Exception as exc:
        logger.warning("Truncated response body read failed: %s", exc)
    return b"".join(chunks)


def _sanitize_url(url: str) -> str:
    """Reduce a webhook URL to ``scheme://host[:port]`` for safe logging.

    Receivers routinely embed a secret token in the path, query string
    or userinfo (e.g. ``https://hook.example.com/in/<token>``); logging
    the raw URL would leak it. We keep only the scheme, host and port —
    enough to identify the receiver without exposing credentials.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return "<unparseable-url>"
    if not parsed.hostname:
        return "<no-host>"
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def _failure(blocking: bool, decision: Decision) -> _HttpResult:
    """Failure-path result: blocking → propagate ``decision`` (DROP /
    RETRY); non-blocking → CONTINUE (fire-and-forget, never stalls
    delivery)."""
    return _HttpResult(decision=decision if blocking else Decision.CONTINUE)


def _classify_response_body(body_bytes: bytes) -> _HttpResult:
    """Parse a 2xx response body into an ``_HttpResult``.

    Empty body or non-JSON body → plain CONTINUE.

    JSON shape (all keys optional):
      - ``action``: ``"drop"`` short-circuits delivery; ``"retry"`` asks
        us to re-queue the inbound task; anything else (``"accept"``,
        missing) → CONTINUE.
      - ``is_spam``: bool; overrides the pipeline's spam verdict.
      - ``add_labels``: list of label UUID strings; the pipeline
        validates them against the destination mailbox.
    """
    if not body_bytes:
        return _HttpResult()
    try:
        # ``json.loads`` accepts bytes natively and raises ValueError
        # (incl. JSONDecodeError) on anything malformed. Deeply-nested
        # JSON raises RecursionError (a RuntimeError, not a ValueError),
        # so catch it too — a misbehaving receiver must never escape this
        # parser and stall the message on an uncaught exception.
        payload = json.loads(body_bytes)
    except (ValueError, RecursionError):
        return _HttpResult()
    if not isinstance(payload, dict):
        return _HttpResult()

    result = _HttpResult()

    action = payload.get("action")
    if isinstance(action, str):
        action = action.lower()
        if action == "drop":
            result.decision = Decision.DROP
        elif action == "retry":
            result.decision = Decision.RETRY

    is_spam = payload.get("is_spam")
    if isinstance(is_spam, bool):
        result.is_spam_override = is_spam

    labels = payload.get("add_labels")
    if isinstance(labels, list):
        for item in labels[:MAX_LABELS_PER_RESPONSE]:
            if not isinstance(item, str):
                continue
            try:
                # Normalise to canonical UUID string; rejects garbage
                # before it ever hits the DB.
                result.labels.add(str(uuid_module.UUID(item)))
            except ValueError:
                continue

    assign_to = payload.get("assign_to")
    if isinstance(assign_to, list):
        # Receiver-supplied OIDC emails. Light filter only: must be a
        # non-empty string containing '@'. Lowercased + deduped while
        # preserving order so a multi-email payload assigns in a
        # predictable sequence. The pipeline does the real work
        # (resolve to User, check editor rights, skip ambiguous).
        seen: Set[str] = set()
        for item in assign_to[:MAX_ASSIGN_TO_PER_RESPONSE]:
            if not isinstance(item, str):
                continue
            email = item.strip().lower()
            if not email or "@" not in email or email in seen:
                continue
            seen.add(email)
            result.assign_to.append(email)

    # Bool flags. ``true``-only semantics — see ``_HttpResult``.
    for key in (
        "mark_starred",
        "mark_read",
        "mark_trashed",
        "mark_archived",
        "skip_autoreply",
    ):
        if payload.get(key) is True:
            setattr(result, key, True)

    reply_draft = payload.get("reply_draft")
    if isinstance(reply_draft, dict):
        candidate = reply_draft.get("template")
        if isinstance(candidate, str):
            try:
                # Normalise to canonical UUID; rejects garbage before
                # the DB lookup.
                result.reply_draft_template_id = str(uuid_module.UUID(candidate))
            except ValueError:
                pass

    add_event = payload.get("add_event")
    if isinstance(add_event, list):
        for item in add_event[:MAX_EVENTS_PER_RESPONSE]:
            if not isinstance(item, dict):
                continue
            event_type = item.get("type")
            if event_type == "im":
                content = item.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                # Cap per-comment size — the comment is stored in
                # ``ThreadEvent.data`` JSONB on every inbound, and we
                # don't want a misconfigured receiver to flood the
                # timeline with 60KB blobs.
                if len(content.encode("utf-8")) > MAX_IM_CONTENT_BYTES:
                    content = content.encode("utf-8")[:MAX_IM_CONTENT_BYTES].decode(
                        "utf-8", errors="ignore"
                    )
                # Mirror the existing IM ThreadEvent shape so the
                # pipeline can persist verbatim. ``mentions`` is
                # intentionally empty for webhook-driven IMs: receivers
                # don't know user UUIDs upfront and we don't want
                # email-based mentions sneaking in here without the
                # mention-notification semantics being designed.
                result.events.append({"type": "im", "content": content, "mentions": []})
            # Unknown event types (incl. future ``type=iframe``) are
            # silently dropped here — the classifier doesn't know how
            # to validate them yet. Forward-compatible: new types
            # become live the moment the classifier learns them, with
            # no contract change for receivers that already emit them.

    return result


FORMAT_EML = "eml"
FORMAT_JMAP = "jmap"
# ``jmap_metadata`` is the cheap notification variant: same JMAP
# envelope (headers, from/to/subject, messageId, etc.) but no body
# parts, no bodyValues, no attachments. Receivers that only need the
# "a message arrived" signal can use it without ever seeing the body
# content over the wire.
FORMAT_JMAP_METADATA = "jmap_metadata"
VALID_FORMATS = frozenset({FORMAT_EML, FORMAT_JMAP, FORMAT_JMAP_METADATA})
DEFAULT_FORMAT = FORMAT_EML

USER_AGENT = "Messages-Webhook/1.0"

# Signature scheme tag. Bumped when the algorithm changes so receivers
# can pin the version they accept.
SIGNATURE_SCHEME = "v1"

# JWT in the Authorization header is a short-lived HS256 token covering
# the same envelope as the raw HMAC, intended for receivers that prefer
# a standard JWT verify path (e.g. n8n, Zapier, Make).
JWT_ISSUER = "messages-webhook"
JWT_TTL_SECONDS = 300  # 5 min — same window receivers should accept on the raw HMAC


def _resolve_body(
    body_format: str,
    raw_data: bytes,
    parsed_email: JmapEmail,
) -> Tuple[str, bytes, Any]:
    """Compute (Content-Type, raw bytes to sign, request kwarg).

    The dispatcher needs:
      - the Content-Type to send,
      - the exact byte string the signature is computed over,
      - the kwarg (``data=`` or ``json=``) to hand off to ``requests``.

    JSON is serialised here once so the signature and the wire bytes
    cannot drift (``requests`` would otherwise re-serialise with
    different separators/ordering).
    """
    if body_format == FORMAT_EML:
        return "message/rfc822", raw_data, {"data": raw_data}
    include_body = body_format == FORMAT_JMAP
    payload = build_jmap_email(parsed_email, include_body=include_body)
    # ``separators=(",", ":")`` produces the compact bytes we sign.
    # Hand the same bytes to ``requests`` via ``data=`` so what we sign
    # is exactly what we POST.
    body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return "application/json", body_bytes, {"data": body_bytes}


def _sign(secret: str, timestamp: str, body_bytes: bytes) -> str:
    """Stripe-style HMAC: HMAC-SHA256 over ``{timestamp}.{body}``.

    Returns hex digest. Receivers MUST compute the same and compare
    constant-time, and SHOULD reject timestamps older than ~5 minutes.
    """
    msg = timestamp.encode("ascii") + b"." + body_bytes
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _sign_jwt(
    secret: str,
    *,
    channel: models.Channel,
    mailbox: models.Mailbox,
    body_bytes: bytes,
    issued_at: int,
) -> str:
    """Build an HS256 JWT for ``Authorization: Bearer …``.

    Claims:
      - ``iss`` — fixed string so receivers can pin issuer.
      - ``iat`` / ``exp`` — short TTL, prevents replay.
      - ``jti`` — random nonce, for receivers that dedupe replays
        beyond timestamp checks.
      - ``sub`` — the destination mailbox (informational).
      - ``cid`` — channel id (matches ``X-StMsg-Channel-Id``).
      - ``body_sha256`` — hex SHA-256 of the request body. Lets the
        receiver bind the JWT to the exact bytes posted, rather than
        trusting transport.

    Encoded with HS256 using the channel's shared secret — receivers
    verify with the same key.
    """
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    claims = {
        "iss": JWT_ISSUER,
        "iat": issued_at,
        "exp": issued_at + JWT_TTL_SECONDS,
        "jti": secrets.token_urlsafe(16),
        "sub": str(mailbox),
        "cid": str(channel.id),
        "body_sha256": body_hash,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


# --- channel lookup --- #


def find_webhook_channels_for_mailbox(
    mailbox: models.Mailbox,
) -> List[models.Channel]:
    """Return every webhook Channel that fires for ``mailbox``.

    Includes:
      - mailbox-scoped channels bound to this mailbox
      - maildomain-scoped channels bound to this mailbox's domain
      - global channels (instance-wide; admin/CLI-only to create)

    Phase filtering is done by the caller because the same channel set is
    read twice (before- and after-spam).
    """
    return list(
        models.Channel.objects.filter(
            Q(type=enums.ChannelTypes.WEBHOOK)
            & (
                Q(
                    scope_level=enums.ChannelScopeLevel.MAILBOX,
                    mailbox=mailbox,
                )
                | Q(
                    scope_level=enums.ChannelScopeLevel.MAILDOMAIN,
                    maildomain=mailbox.domain,
                )
                | Q(scope_level=enums.ChannelScopeLevel.GLOBAL)
            )
        )
    )


# --- payload builders --- #


def _utcdate(value: Any) -> Optional[str]:
    """Format a datetime as a JMAP ``UTCDate`` (RFC 3339 with ``Z`` suffix).

    Falls back to the raw value if it isn't a datetime — the parser may
    have given us a pre-formatted string. ``None`` stays ``None``.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def _strip_body_part(part: Dict[str, Any]) -> Dict[str, Any]:
    """A JMAP ``EmailBodyPart`` without our parser's project extensions.

    ``content`` (raw bytes on attachment parts) and ``sha256`` are
    parser extensions, NOT RFC 8621. Attachment bytes in particular are
    never embedded in the JSON body — JMAP keeps them behind a
    ``blobId`` (which we don't have at webhook-fire time), and raw bytes
    aren't JSON-serialisable anyway. Receivers that need the bytes use
    ``format=eml``.
    """
    return {k: v for k, v in part.items() if k not in ("content", "sha256")}


def build_jmap_email(
    parsed_email: JmapEmail, *, include_body: bool = True
) -> Dict[str, Any]:
    """Project the parsed JMAP ``Email`` object into the webhook payload.

    ``parse_email`` already returns a strict JMAP Email object
    (RFC 8621 §4.1), so this is mostly a copy. We stamp ``receivedAt``
    (the moment the webhook fires) and strip the parser's project
    extensions (``_ext`` and per-part ``content`` / ``sha256``) so the
    body is strict JMAP. Storage-time fields (``id``, ``blobId``,
    ``threadId``, ``mailboxIds``, ``keywords``) don't exist at
    webhook-fire time and are simply absent.

    With ``include_body=False`` the body parts, ``bodyValues`` and
    ``attachments`` are dropped — receivers get a notification-only
    payload (subject + envelope addresses + headers) without the
    message body content ever leaving the instance over the wire.
    ``hasAttachment`` is preserved so receivers can still tell whether
    the message had any.
    """
    email: Dict[str, Any] = dict(parsed_email)
    email.pop("_ext", None)  # project extension, not strict JMAP
    email["receivedAt"] = _utcdate(datetime.now(timezone.utc))

    if include_body:
        email["textBody"] = [
            _strip_body_part(p) for p in parsed_email.get("textBody") or []
        ]
        email["htmlBody"] = [
            _strip_body_part(p) for p in parsed_email.get("htmlBody") or []
        ]
        email["attachments"] = [
            _strip_body_part(p) for p in parsed_email.get("attachments") or []
        ]
    else:
        for key in (
            "textBody",
            "htmlBody",
            "bodyValues",
            "bodyStructure",
            "attachments",
            "preview",
        ):
            email.pop(key, None)

    return email


# --- envelope headers --- #


def _envelope_headers(
    *,
    channel: models.Channel,
    phase: str,
    mailbox: models.Mailbox,
    recipient_email: str,
    is_spam: Optional[bool],
) -> Dict[str, str]:
    """Build the ``X-StMsg-*`` envelope headers attached to every webhook
    POST regardless of body format. Same shape for ``eml`` and ``jmap``.

    The message-id is intentionally *not* a header: every body format
    already carries it (``messageId`` in the jmap variants, the raw
    ``Message-ID:`` header in ``eml``), so a header would only duplicate
    it.
    """
    if is_spam is None:
        spam_value = "unknown"
    else:
        spam_value = "true" if is_spam else "false"
    return {
        "User-Agent": USER_AGENT,
        "X-StMsg-Event": enums.WebhookEvents.MESSAGE_INBOUND.value,
        "X-StMsg-Phase": phase,
        "X-StMsg-Channel-Id": str(channel.id),
        "X-StMsg-Mailbox": str(mailbox),
        "X-StMsg-Recipient": recipient_email,
        "X-StMsg-Is-Spam": spam_value,
    }


# --- dispatch --- #


class UserWebhookStep:
    """Pipeline ``Step`` wrapping one webhook ``Channel``.

    Each matching channel becomes its own step in the inbound pipeline
    (one per phase). On call, the step POSTs the configured body to
    the channel URL, classifies the response, applies any
    ``is_spam`` override and ``labels`` to ``ctx``, and returns a
    ``Decision``:

      - non-blocking → always ``CONTINUE`` (fire-and-forget; failures
        only logged)
      - blocking:
          * 2xx + ``{"action":"drop"}`` → DROP (the *only* path to DROP)
          * 2xx + ``{"action":"retry"}`` → RETRY
          * 2xx + anything else → CONTINUE (with optional side effects)
          * any non-2xx (4xx / 5xx / 3xx) → RETRY
          * SSRF / missing secret / unknown auth_method → RETRY
          * timeout / connection / generic transport → RETRY

    A webhook error never drops the user's email — only an explicit
    ``{"action": "drop"}`` does. Every failure is held for retry,
    bounded by the pipeline's 48-hour quarantine window.
    """

    def __init__(self, channel: models.Channel, phase: str):
        self.channel = channel
        self.phase = phase
        # Phase suffix in the name lets the task return value carry
        # "which phase did this drop happen at" without a separate field.
        self.name = f"webhook[{channel.id}]:{phase}"

    def __call__(self, ctx: InboundContext) -> Decision:
        cfg = self.channel.settings or {}
        url = cfg["url"]  # validated at channel-create time
        body_format = cfg.get("format", DEFAULT_FORMAT)
        blocking = bool(cfg.get("blocking", False))

        headers = _envelope_headers(
            channel=self.channel,
            phase=self.phase,
            mailbox=ctx.mailbox,
            recipient_email=ctx.recipient_email,
            is_spam=ctx.is_spam,
        )
        result = self._post(
            url=url,
            body_format=body_format,
            headers=headers,
            ctx=ctx,
            blocking=blocking,
        )
        if result.is_spam_override is not None:
            ctx.is_spam = result.is_spam_override
        ctx.labels |= result.labels
        if result.assign_to:
            # Defer the actual assignment until after the thread
            # exists (post-message-creation). Each blocking webhook
            # that asked gets its own ThreadEvent ASSIGN, attributed
            # to this channel.
            ctx.pending_assigns.append((self.channel.id, result.assign_to))
        # Bool flags OR-merge: any blocking webhook saying true sticks.
        ctx.mark_starred = ctx.mark_starred or result.mark_starred
        ctx.mark_read = ctx.mark_read or result.mark_read
        ctx.mark_trashed = ctx.mark_trashed or result.mark_trashed
        ctx.mark_archived = ctx.mark_archived or result.mark_archived
        ctx.skip_autoreply = ctx.skip_autoreply or result.skip_autoreply
        for event in result.events:
            # Per-event attribution like assigns — one ThreadEvent per
            # add_event entry, with channel set to the firing webhook.
            ctx.pending_events.append((self.channel.id, event))
        if result.reply_draft_template_id:
            # Defer template lookup + draft creation until after the
            # message + thread land. Each blocking webhook that asked
            # produces its own draft, channel-attributed.
            ctx.pending_drafts.append((self.channel.id, result.reply_draft_template_id))
        return result.decision

    def _post(
        self,
        *,
        url: str,
        body_format: str,
        headers: Dict[str, str],
        ctx: InboundContext,
        blocking: bool,
    ) -> _HttpResult:
        content_type, body_bytes, body_kwargs = _resolve_body(
            body_format, ctx.raw_data, ctx.parsed_email
        )

        secret = (self.channel.encrypted_settings or {}).get("secret")
        if not secret:
            # The create path always mints a secret; a row without one
            # is misconfigured. We can't sign the POST, so we hold for
            # RETRY rather than drop the user's mail — re-minting the
            # secret lets the next sweep deliver. A webhook failure must
            # never silently discard the email (only an explicit
            # ``{"action": "drop"}`` on a 2xx does that).
            logger.warning(
                "Webhook channel %s has no secret — holding for retry",
                self.channel.id,
            )
            return _failure(blocking, Decision.RETRY)

        auth_headers = self._build_auth_headers(secret, body_bytes, ctx.mailbox)
        if auth_headers is None:
            # Unknown/misconfigured auth_method — same reasoning: hold
            # for retry, never drop the email on our config error.
            return _failure(blocking, Decision.RETRY)

        signed_headers = {
            **headers,
            "Content-Type": content_type,
            **auth_headers,
        }
        kwargs = {"headers": signed_headers, **body_kwargs}

        # ``stream=True`` lets us cap the response body we actually
        # read — a misconfigured receiver returning a multi-GB error
        # page must not OOM the worker.
        try:
            response = SSRFSafeSession().post(
                url, timeout=WEBHOOK_TIMEOUT, stream=True, **kwargs
            )
        except SSRFValidationError as exc:
            # SSRF block is a config error on our side (the URL points at a
            # disallowed address). Hold for RETRY rather than drop — fixing
            # the URL lets the next sweep deliver. We never discard the
            # user's mail because of a webhook/config failure.
            logger.warning(
                "Webhook channel %s rejected by SSRF for url=%s: %s",
                self.channel.id,
                _sanitize_url(url),
                exc,
            )
            return _failure(blocking, Decision.RETRY)
        except Exception as exc:
            # Timeout, connection refused, DNS, unknown transport-level
            # failure: all transient on the blocking path. The 48-hour
            # quarantine window in the pipeline runner bounds the retries.
            # Log only the exception *type*, not its message or traceback:
            # requests/urllib3 errors embed the full request URL (path +
            # query), which is exactly where receivers carry secret tokens,
            # so ``exc``/``exc_info`` would bypass ``_sanitize_url``.
            logger.warning(
                "Webhook channel %s network error (%s) for url=%s",
                self.channel.id,
                type(exc).__name__,
                _sanitize_url(url),
            )
            return _failure(blocking, Decision.RETRY)

        try:
            status = response.status_code
            if 200 <= status < 300:
                if not blocking:
                    # Non-blocking webhooks never influence delivery —
                    # ignore the body entirely. Avoids surprises if a
                    # receiver accidentally returns {"action":"drop"}.
                    return _HttpResult()
                body_bytes_response = _read_capped_body(response)
                result = _classify_response_body(body_bytes_response)
                if result.decision == Decision.DROP:
                    logger.info(
                        "Webhook channel %s requested DROP via response body for url=%s",
                        self.channel.id,
                        _sanitize_url(url),
                    )
                return result

            logger.info(
                "Webhook channel %s returned status %s for url=%s",
                self.channel.id,
                status,
                _sanitize_url(url),
            )
            # Any non-2xx status is a transient failure → RETRY. A
            # blocking webhook DROPs an email *only* when it explicitly
            # returns ``{"action": "drop"}`` with a 2xx (handled above).
            # A receiver bug that answers 4xx must never cost the user
            # their mail — the 7-day retry budget bounds the hold.
            return _failure(blocking, Decision.RETRY)
        finally:
            response.close()

    def _build_auth_headers(
        self,
        secret: str,
        body_bytes: bytes,
        mailbox: models.Mailbox,
    ) -> Optional[Dict[str, str]]:
        """Return the auth headers for the channel's ``auth_method``,
        or ``None`` when the channel is misconfigured (caller fails
        closed)."""
        auth_method = (self.channel.settings or {}).get("auth_method")

        if auth_method == "api_key":
            # Derived from the root secret via HMAC. The raw root
            # never touches the wire — a receiver-side log leak of
            # this value reveals nothing about the root, so HMAC/JWT
            # verification remains unforgeable.
            return {"X-StMsg-Api-Key": self.channel.get_webhook_api_key()}

        if auth_method == "jwt":
            # HMAC signature over the body + short-TTL HS256 JWT,
            # both keyed by the root secret.
            now = int(time.time())
            timestamp = str(now)
            signature = _sign(secret, timestamp, body_bytes)
            bearer = _sign_jwt(
                secret,
                channel=self.channel,
                mailbox=mailbox,
                body_bytes=body_bytes,
                issued_at=now,
            )
            return {
                "X-StMsg-Webhook-Timestamp": timestamp,
                "X-StMsg-Webhook-Signature": f"{SIGNATURE_SCHEME}={signature}",
                "Authorization": f"Bearer {bearer}",
            }

        # Settings validator forbids creating a webhook channel without
        # a valid auth_method; an existing row with a missing/unknown
        # value is misconfigured.
        logger.warning(
            "Webhook channel %s has missing/unknown auth_method=%r — skipping",
            self.channel.id,
            auth_method,
        )
        return None


def webhook_steps_for_mailbox(
    mailbox: models.Mailbox,
    *,
    phase: str,
    channels: Optional[List[models.Channel]] = None,
) -> List[Step]:
    """Build one ``UserWebhookStep`` per matching channel for the phase.

    Channels are filtered here (phase, events, url present, valid
    format) rather than at run time so the pipeline iterator sees a
    flat list of ready-to-call steps.

    ``channels`` may be passed in to reuse a single channel-set query
    across both phases (the set is identical before- and after-spam);
    when omitted it is fetched here.
    """
    if phase not in VALID_PHASES:
        raise ValueError(f"Invalid webhook phase: {phase}")

    if channels is None:
        channels = find_webhook_channels_for_mailbox(mailbox)

    steps: List[Step] = []
    for channel in channels:
        cfg = channel.settings or {}
        if cfg.get("phase", PHASE_AFTER_SPAM) != phase:
            continue
        events = cfg.get("events") or [enums.WebhookEvents.MESSAGE_INBOUND.value]
        if not isinstance(events, list):
            # Validator guarantees a list on write; a non-list here is a
            # misconfigured row. Fail closed — a bare string would make the
            # ``in`` check below match substrings instead of members.
            logger.warning(
                "Webhook channel %s has non-list events=%r — skipping",
                channel.id,
                events,
            )
            continue
        if enums.WebhookEvents.MESSAGE_INBOUND.value not in events:
            continue
        if not cfg.get("url"):
            continue
        body_format = cfg.get("format", DEFAULT_FORMAT)
        if body_format not in VALID_FORMATS:
            # Serializer should have caught this on write — fail
            # closed rather than POST in a shape the receiver wasn't
            # promised.
            logger.warning(
                "Webhook channel %s has invalid format=%r — skipping",
                channel.id,
                body_format,
            )
            continue
        steps.append(UserWebhookStep(channel, phase=phase))
    return steps

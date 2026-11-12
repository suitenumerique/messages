"""Widget channel implementation for receiving messages from web widgets."""

# Attachment handling imports.
import mimetypes
import uuid
from pathlib import Path
import base64

import logging
import re
from html import escape as html_escape
from urllib.parse import urlparse

from django.conf import settings

from drf_spectacular.utils import extend_schema
from jmap_email import ComposeError, compose_email, parse_address
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from core import enums, models
from core.api.permissions import IsAuthenticated
from core.mda.addresses import address_domain
from core.mda.inbound import deliver_inbound_message
from core.mda.utils import compose_options_for, current_sent_at

logger = logging.getLogger(__name__)


# Referer hosts we accept to put in the subject line: DNS names and IPv4.
_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?$", re.IGNORECASE)

# Size limit for each attachment.
# Could be set in settings.
MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024        # 5 MB per file

# Attachment handling helpers to compute filename and mime-type from incoming infos.
#
# resolve_mime_type("photo.jpg", "image/png")   # 'image/png'  (given type wins)
# resolve_mime_type("photo.jpg", None)          # 'image/jpeg' (from extension)
# resolve_mime_type("photo", None)              # 'application/octet-stream'
# resolve_mime_type(None, "")                   # 'application/octet-stream'
def resolve_mime_type(filename: str | None, mime_type: str | None) -> str:
    if mime_type:
        return mime_type
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    return "application/octet-stream"

# resolve_attachment(None, None)                    # ('3f2b8c…a6b.bin', 'application/octet-stream')
# resolve_attachment("report", "application/pdf")   # ('report.pdf', 'application/pdf')
# resolve_attachment("photo.jpg", None)             # ('photo.jpg', 'image/jpeg')
def resolve_attachment(filename: str | None, mime_type: str | None) -> tuple[str, str]:
    mime = resolve_mime_type(filename, mime_type)
    name = Path((filename or "").replace("\\", "/")).name or uuid.uuid4().hex
    if not Path(name).suffix:
        name += mimetypes.guess_extension(mime) or ".bin"
    return name, mime
# [END] Attachment handling helpers

def _is_plausible_hostname(hostname: str) -> bool:
    """True when a Referer host is safe to render as the ticket source."""
    return bool(_HOSTNAME_RE.match(hostname))


class WidgetChannelThrottle(SimpleRateThrottle):
    """Per-channel rate limit for the public widget deliver endpoint.

    The channel id is the literal value embedded in the public HTML snippet,
    so it offers no secrecy — anyone who scrapes it can POST. Keying the
    throttle on the channel (not the source IP) caps the total inbound volume
    a single widget can push into its mailbox regardless of how many IPs the
    caller rotates through, bounding mailbox/blob/Contact/Thread growth and
    per-message AI labeling cost.
    """

    scope = "widget_inbound_channel"

    def get_cache_key(self, request, view):
        auth = getattr(request, "auth", None)
        channel = auth.get("channel") if isinstance(auth, dict) else None
        if channel is None:
            return None  # Unauthenticated — auth layer will reject it.
        return self.cache_format % {"scope": self.scope, "ident": str(channel.id)}


class WidgetIPThrottle(SimpleRateThrottle):
    """Per-IP burst limit, layered under the per-channel cap above.

    Stops a single source from saturating a channel's quota and gives a
    cheaper first line of defense against floods from one host.
    """

    scope = "widget_inbound_ip"

    def get_cache_key(self, request, view):
        # Key on REMOTE_ADDR, not DRF's get_ident(): get_ident() prefers the
        # raw X-Forwarded-For header (client-spoofable, and only trustworthy
        # when NUM_PROXIES is configured — this project does not use it).
        # Instead REMOTE_ADDR is normalized to the real client IP by
        # XForwardedForMiddleware when USE_X_FORWARDED_FOR is enabled, which is
        # the IP the rest of this view already trusts.
        ident = request.META.get("REMOTE_ADDR")
        return self.cache_format % {"scope": self.scope, "ident": ident}


class WidgetAuthentication(BaseAuthentication):
    """
    Custom authentication for widget endpoints using channel_id header
    Returns None or (user, auth)
    """

    def authenticate(self, request):
        # Try API key authentication first
        channel_id = request.headers.get("X-Channel-ID")
        if not channel_id:
            raise AuthenticationFailed("Missing channel_id")

        # Only allow widget-type channels. A paused (is_active=False) widget
        # is treated as invalid so it stops accepting messages without
        # revealing that the channel merely exists but is disabled.
        try:
            channel = models.Channel.objects.get(
                id=channel_id, type="widget", is_active=True
            )
        except models.Channel.DoesNotExist as e:
            raise AuthenticationFailed("Invalid channel_id") from e

        return (None, {"channel": channel})


class InboundWidgetViewSet(viewsets.GenericViewSet):
    """Handles incoming messages from web widgets."""

    # Channel metadata
    CHANNEL_TYPE = "widget"
    CHANNEL_DESCRIPTION = "Web widgets and forms"

    permission_classes = [IsAuthenticated]
    authentication_classes = [WidgetAuthentication]

    def get_throttles(self):
        """Rate-limit only the public ``deliver`` endpoint.

        ``config`` is a cheap idempotent read fetched on widget load and is
        left unthrottled; ``deliver`` is the write path an attacker could
        abuse, so it carries both the per-channel and per-IP throttles.
        """
        if getattr(self, "action", None) == "deliver":
            return [WidgetChannelThrottle(), WidgetIPThrottle()]
        return super().get_throttles()

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["get"],
        url_path="config",
        url_name="inbound-widget-config",
    )
    def config(self, request):
        """Return the configuration for the widget."""

        auth_data = request.auth
        channel = auth_data["channel"]

        return Response(
            {"success": True, "config": (channel.settings or {}).get("config") or {}}
        )

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="deliver",
        url_name="inbound-widget-deliver",
    )
    def deliver(self, request):
        """Handle incoming widget message."""

        data = request.data
        auth_data = request.auth
        channel = auth_data["channel"]

        sender_email = data.get("email")
        message_text = data.get("textBody", "")

        if not sender_email:
            return Response(
                {"detail": "Missing email"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Cap the body so a caller can't fill blob storage with one giant
        # message. ``message_text`` is the only unbounded field; it is expanded
        # into both the text and HTML parts of the stored MIME, so bounding it
        # bounds the resulting blob. Mirrors the MAX_INCOMING_EMAIL_SIZE limit
        # the MTA path already enforces.
        if len(message_text.encode("utf-8")) > settings.MAX_INCOMING_EMAIL_SIZE:
            return Response(
                {"detail": "Message too large"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        # Validate through the same parser the rest of the pipeline
        # uses. ``parse_address`` is strict by default and returns
        # ``("", "")`` on garbage input.
        _, normalised_sender = parse_address(sender_email)
        if not normalised_sender:
            return Response(
                {"detail": "Invalid email format"}, status=status.HTTP_400_BAD_REQUEST
            )
        sender_email = normalised_sender

        if not message_text:
            return Response(
                {"detail": "Missing message"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Get the target mailbox
        mailbox = channel.mailbox
        if not mailbox:
            return Response(
                {"detail": "No mailbox configured for this channel"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if mailbox.contact:
            target_email = mailbox.contact.email
            target_name = mailbox.contact.name
        else:
            target_email = str(mailbox)
            target_name = str(mailbox)

        def sanitize_header(header: str) -> str:
            return header.replace("\r", "").replace("\n", "")[0:1000]

        # ``Return-Path`` (envelope MAIL FROM) and the widget ``Received`` are
        # immutable ingest facts → baked as headers in the one blob. The
        # sender-auth "none" baseline for widget mail is set structurally in the
        # pipeline (``postmark["auth"]``), not baked here. ``X-StMsg-Widget-
        # Referer`` stays a header (immutable ingest provenance).
        prepend_headers = [("Return-Path", f"<{sender_email}>")]
        source_name = "widget"
        if request.META.get("HTTP_REFERER"):
            referer = sanitize_header(request.META.get("HTTP_REFERER"))
            prepend_headers.append(("X-StMsg-Widget-Referer", referer))
            try:
                # The Referer is client-controlled: only use it for display,
                # and only if it looks like a bare host (``netloc`` would also
                # carry userinfo and port).
                hostname = urlparse(referer).hostname
                if hostname and _is_plausible_hostname(hostname):
                    source_name = hostname
            except ValueError as e:
                logger.warning(
                    "Cannot retrieve hostname from referer %s: %s", referer, e
                )

        prepend_headers.append(
            (
                "Received",
                f"from widget ({sanitize_header(request.META.get('REMOTE_ADDR'))})",
            ),
        )

        # Build subject from template or use default
        # Template can use {referer_domain} placeholder (same format as signature templates)
        default_subject_template = "Message from {referer_domain}"
        subject_template = (channel.settings or {}).get(
            "subject_template", default_subject_template
        )

        # Replace template variables
        subject = subject_template.replace("{referer_domain}", source_name)

        # Sanitize subject to prevent header injection (strip newlines/carriage returns)
        subject = subject.replace("\r", "").replace("\n", "")

        # Add attachments if any.
        # Received attachments are structured as follow:
        # [{ "type": "application/octet-stream",
        #    "name": pdf.name,
        #    "disposition": "attachment",
        #    "content": base64_encoded_content(),}]
        #
        # JMAP attachements must be structured as follow:
        # [{
        #     "name": "invoice.pdf",
        #     "type": "application/pdf",
        #     "content": raw_bytes_content(),
        #     "disposition": "attachment",
        # }]

        # Missing key or null means no attachments; any other non-list value is rejected.
        incoming_attachments = data.get("attachments")
        if incoming_attachments is None:
            incoming_attachments = []
        # Check that incoming_attachments is a list.
        if not isinstance(incoming_attachments, list):
            return Response(
                {"detail": "Invalid attachments"}, status=status.HTTP_400_BAD_REQUEST
            )
        # prepare outgoing attachments list that will stay empty if no attachment exists.
        outgoing_attachments = []
        attachments_total_bytes = 0

        for src_att in incoming_attachments:
            # Check that each incoming attachment is a dict and their content is a string.
            if not isinstance(src_att, dict) or not isinstance(src_att.get("content"), str):
                return Response(
                    {"detail": "Invalid attachment"}, status=status.HTTP_400_BAD_REQUEST
                )
            name = src_att.get("name") if isinstance(src_att.get("name"), str) else None
            mime = src_att.get("type") if isinstance(src_att.get("type"), str) else None
            b64_encoded_content = src_att["content"]

            # Check if attachment size exceeds the limit.
            # base64 is ~4/3 the decoded size: reject early without decoding a huge string
            # Django has its own DATA_UPLOAD_MAX_MEMORY_SIZE that will reject large uploads if exceeded.
            if len(b64_encoded_content) > 4 * ((MAX_ATTACHMENT_SIZE + 2) // 3):
                return Response(
                    {"detail": "Attachment too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
            try:
                base64_decoded_content = base64.b64decode(b64_encoded_content, validate=True)
            except ValueError:  # binascii.Error subclasses ValueError
                return Response(
                    {"detail": "Invalid attachment encoding"}, status=status.HTTP_400_BAD_REQUEST,
                )
            # Reject empty attachment.
            if not base64_decoded_content:
                return Response(
                    {"detail": "Attachment is empty"}, status=status.HTTP_400_BAD_REQUEST
                )

            # Reject if this attachment or total attachments size is too big.
            attachments_total_bytes += len(base64_decoded_content)
            if len(base64_decoded_content) > MAX_ATTACHMENT_SIZE or attachments_total_bytes > settings.MAX_INCOMING_EMAIL_SIZE:
                return Response(
                    {"detail": "Attachment too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            att_name, att_type = resolve_attachment(name, mime)
            outgoing_attachments.append({
                "name": att_name,
                "type": att_type,
                "disposition": "attachment",
                # raw bytes as `Mailbox.create_blob()` method (called by `_process_attachments`) expects.
                "content": base64_decoded_content,
            })

        # Build a JMAP-like structured format that we could have got from parse_email()

        parsed_email = {
            "subject": subject,
            "from": [{"email": sender_email}],
            "to": [{"name": target_name, "email": target_email}],
            "sentAt": current_sent_at(),
            "htmlBody": [{"content": html_escape(message_text).replace("\n", "<br/>")}],
            "textBody": [{"content": message_text}],
            # Add attachments if any.
            "attachments": outgoing_attachments,
        }

        # A widget submission is delivered to a local mailbox, never
        # retransmitted over SMTP, so an RFC 6531 sender costs nothing here
        # and keeping it means the mailbox owner can actually reply. The
        # composer can still refuse a malformed addr-spec, which is a
        # property of what this caller typed into a public form, so it is a
        # 400 like the shape check above, not a 500.
        try:
            raw_mime = compose_email(
                parsed_email,
                prepend_headers=prepend_headers,
                options=compose_options_for([sender_email, target_email]),
            )
        except ComposeError:
            logger.info(
                "Widget submission rejected: cannot compose MIME for sender "
                "at domain %r",
                address_domain(sender_email),
            )
            return Response(
                {"detail": "Invalid email format"}, status=status.HTTP_400_BAD_REQUEST
            )

        delivered = deliver_inbound_message(
            target_email,
            parsed_email,
            raw_mime,
            channel=channel,
            envelope={
                "origin": enums.InboundOrigin.WIDGET,
                "mail_from": sender_email,
                "rcpt_to": target_email,
                "ip": request.META.get("REMOTE_ADDR", ""),
            },
        )

        if not delivered:
            return Response(
                {"detail": "Failed to deliver message"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "Successfully created message from widget for channel %s, sender "
            "at domain %r",
            channel.id,
            address_domain(sender_email),
        )

        return Response(
            {
                "success": True,
            }
        )

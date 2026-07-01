"""Widget channel implementation for receiving messages from web widgets."""

import logging
from html import escape as html_escape
from urllib.parse import urlparse

from django.conf import settings

from drf_spectacular.utils import extend_schema
from jmap_email import compose_email, parse_address
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from core import enums, models
from core.api.permissions import IsAuthenticated
from core.mda.inbound import deliver_inbound_message
from core.mda.utils import current_sent_at

logger = logging.getLogger(__name__)


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

        # Only allow widget-type channels
        try:
            channel = models.Channel.objects.get(id=channel_id, type="widget")
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
                parsed_referer = urlparse(referer)
                if parsed_referer.netloc:
                    source_name = parsed_referer.netloc
            except ValueError as e:
                logger.warning("Cannot retrieve netloc from referer %s: %s", referer, e)

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

        # Build a JMAP-like structured format that we could have got from parse_email()

        parsed_email = {
            "subject": subject,
            "from": [{"email": sender_email}],
            "to": [{"name": target_name, "email": target_email}],
            "sentAt": current_sent_at(),
            "htmlBody": [{"content": html_escape(message_text).replace("\n", "<br/>")}],
            "textBody": [{"content": message_text}],
        }

        delivered = deliver_inbound_message(
            target_email,
            parsed_email,
            compose_email(parsed_email, prepend_headers=prepend_headers),
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
            "Successfully created message from widget for channel %s, sender: %s",
            channel.id,
            sender_email,
        )

        return Response(
            {
                "success": True,
            }
        )

"""Brevo inbound channel implementation for handling email from Brevo parse webhooks."""

import hashlib
import logging
import secrets
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import models
from core.mda.inbound import check_local_recipients, deliver_inbound_message
from core.mda.rfc5322 import compose_email

logger = logging.getLogger(__name__)


class BrevoAuthentication(BaseAuthentication):
    """
    Custom authentication for Brevo webhook endpoints.

    Supports two authentication methods:
    1. Channel ID authentication (X-Channel-ID header) - for channel-specific webhooks
    2. HMAC signature validation (X-Brevo-Signature header) - for global webhooks

    Returns None or (user, auth)
    """

    def authenticate(self, request):
        channel_id = request.headers.get("X-Channel-ID")
        if channel_id:
            return self._authenticate_by_channel_id(channel_id)

        signature = request.headers.get("X-Brevo-Signature")
        if signature:
            return self._authenticate_by_signature(request, signature)

        raise AuthenticationFailed("Missing authentication credentials")

    def _authenticate_by_channel_id(self, channel_id: str):
        try:
            channel = models.Channel.objects.get(id=channel_id, type="brevo")
        except (models.Channel.DoesNotExist, ValidationError) as e:
            raise AuthenticationFailed("Invalid channel_id") from e
        return (None, {"channel": channel, "auth_type": "channel_id"})

    def _authenticate_by_signature(self, request, signature: str):
        secret = getattr(settings, "BREVO_WEBHOOK_SECRET", None)
        if not secret:
            raise AuthenticationFailed("Brevo webhook secret not configured")

        body = request.body
        expected_signature = hashlib.sha256(secret.encode() + body).hexdigest()
        if not secrets.compare_digest(signature, expected_signature):
            raise AuthenticationFailed("Invalid signature")

        return (None, {"auth_type": "hmac"})

    def authenticate_header(self, request):
        return 'Bearer realm="Brevo"'


def convert_brevo_payload_to_parsed_email(item: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Brevo parsed email payload to internal email format."""
    from_email = item.get("From", {})
    to_emails = item.get("To", [])
    cc_emails = item.get("Cc", [])
    reply_to = item.get("ReplyTo")

    subject = item.get("Subject", "(no subject)")
    extracted_message = item.get("ExtractedMarkdownMessage", "")
    raw_html_body = item.get("RawHtmlBody")
    raw_text_body = item.get("RawTextBody")
    in_reply_to = item.get("InReplyTo")
    sent_at = item.get("SentAtDate")

    html_body = ""
    if raw_html_body:
        html_body = raw_html_body
    elif extracted_message:
        html_body = extracted_message.replace("\n", "<br/>")

    text_body = raw_text_body or extracted_message or ""

    parsed_email: Dict[str, Any] = {
        "subject": subject,
        "from": {
            "email": from_email.get("Address", ""),
            "name": from_email.get("Name"),
        },
        "to": [
            {
                "email": to.get("Address", ""),
                "name": to.get("Name"),
            }
            for to in to_emails
        ],
        "cc": [
            {
                "email": cc.get("Address", ""),
                "name": cc.get("Name"),
            }
            for cc in cc_emails
        ],
        "headers": {},
    }

    if reply_to:
        parsed_email["reply_to"] = {
            "email": reply_to.get("Address", ""),
            "name": reply_to.get("Name"),
        }

    if in_reply_to:
        parsed_email["in_reply_to"] = in_reply_to
        parsed_email["references"] = in_reply_to

    if sent_at:
        try:
            parsed_email["date"] = parsedate_to_datetime(sent_at)
        except (ValueError, TypeError):
            parsed_email["date"] = timezone.now()
    else:
        parsed_email["date"] = timezone.now()

    if html_body:
        parsed_email["htmlBody"] = [{"content": html_body}]
    if text_body:
        parsed_email["textBody"] = [{"content": text_body}]

    return parsed_email


class InboundBrevoViewSet(viewsets.GenericViewSet):
    """Handles incoming email messages from Brevo inbound parse webhooks."""

    CHANNEL_TYPE = "brevo"
    CHANNEL_DESCRIPTION = "Brevo inbound email parsing"

    permission_classes = [IsAuthenticated]
    authentication_classes = [BrevoAuthentication]

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="webhook",
        url_name="inbound-brevo-webhook",
    )
    def webhook(self, request):
        """Handle incoming Brevo webhook with parsed email(s)."""

        data = request.data
        items = data.get("items", [])

        if not items:
            return Response(
                {"detail": "No items in payload"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(items, list):
            return Response(
                {"detail": "Items must be a list"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        auth_data = request.auth
        channel = auth_data.get("channel")

        results = []
        success_count = 0
        failure_count = 0

        for item in items:
            result = self._process_brevo_item(item, channel)
            if result["success"]:
                success_count += 1
            else:
                failure_count += 1
            results.append(result)

        if failure_count > 0 and success_count == 0:
            return Response(
                {
                    "status": "error",
                    "detail": "Failed to process all messages",
                    "results": results,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if failure_count > 0:
            return Response(
                {
                    "status": "partial_success",
                    "processed": success_count,
                    "failed": failure_count,
                    "results": results,
                },
                status=status.HTTP_207_MULTI_STATUS,
            )

        logger.info(
            "Successfully processed %d Brevo inbound messages",
            success_count,
        )
        return Response(
            {
                "status": "ok",
                "processed": success_count,
                "results": results,
            }
        )

    def _process_brevo_item(self, item: Dict[str, Any], channel: Optional[models.Channel]) -> Dict[str, Any]:
        """Process a single Brevo email item."""
        try:
            from_email = item.get("From", {}).get("Address", "")
            if not from_email:
                return {"success": False, "error": "Missing From address"}

            recipients = item.get("Recipients", [])
            if not recipients:
                recipients = [r.get("Address") for r in item.get("To", []) if r.get("Address")]
            if not recipients:
                return {"success": False, "error": "No recipients found"}

            recipient_emails = [r for r in recipients if r]
            if not recipient_emails:
                return {"success": False, "error": "No valid recipient addresses"}

            local_recipients = check_local_recipients(recipient_emails)

            parsed_email = convert_brevo_payload_to_parsed_email(item)
            prepend_headers = [
                ("X-Brevo-Webhook", "inbound"),
                ("Received", "from brevo-inbound"),
            ]

            raw_email = compose_email(parsed_email, prepend_headers=prepend_headers)

            delivered_count = 0
            for recipient in recipient_emails:
                if recipient in local_recipients:
                    delivered = deliver_inbound_message(
                        recipient,
                        parsed_email,
                        raw_email,
                        channel=channel,
                        skip_inbound_queue=True,
                    )
                    if delivered:
                        delivered_count += 1

            return {
                "success": delivered_count > 0,
                "message_id": item.get("MessageId"),
                "delivered": delivered_count,
                "recipients": len(recipient_emails),
            }

        except Exception as e:
            logger.exception("Error processing Brevo item: %s", e)
            return {"success": False, "error": str(e)}

    @extend_schema(exclude=True)
    @action(
        detail=False,
        methods=["post"],
        url_path="check",
        url_name="inbound-brevo-check",
    )
    def check(self, request):
        """Check which recipients are locally deliverable."""
        data = request.data
        addresses = data.get("addresses", [])
        if not addresses or not isinstance(addresses, list):
            return Response(
                {"detail": "Missing addresses"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        local_addresses = check_local_recipients(addresses)
        results = {address: address in local_addresses for address in addresses}
        return Response(results)

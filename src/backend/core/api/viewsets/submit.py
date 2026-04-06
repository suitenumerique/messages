"""Generic outbound email submission endpoint.

POST /api/v1.0/submit/
Accepts a raw RFC 5322 message and sends it from a mailbox.
Creates a Message via the inbound pipeline (with ``is_outbound=True``),
then runs ``prepare_outbound_message`` synchronously (DKIM signing, blob
creation) and dispatches SMTP delivery asynchronously via Celery.
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core import models
from core.api.authentication import ChannelApiKeyAuthentication
from core.api.permissions import channel_scope
from core.enums import MAILBOX_ROLES_CAN_SEND, ChannelApiKeyScope
from core.mda.inbound_create import _create_message_from_inbound
from core.mda.outbound import prepare_outbound_message
from core.mda.outbound_tasks import send_message_task
from core.mda.rfc5322 import EmailParseError, parse_email_message

logger = logging.getLogger(__name__)


class SubmitRawEmailView(APIView):
    """Submit a pre-composed RFC 5322 email for delivery from a mailbox.

    POST /api/v1.0/submit/
    Content-Type: message/rfc822
    Headers:
        X-Channel-Id: <channel uuid>  (api_key channel with messages:send scope)
        X-API-Key:    <raw secret>
        X-Mail-From:  <mailbox uuid>  (UUID of the sending mailbox)
        X-Rcpt-To:    <addr>[,<addr>] (comma-separated recipient addresses)

    The endpoint creates a Message record, DKIM-signs the raw MIME
    synchronously, and dispatches SMTP delivery via Celery.

    Returns: ``{"message_id": "<…>", "status": "accepted"}`` (HTTP 202).
    """

    authentication_classes = [ChannelApiKeyAuthentication]
    permission_classes = [channel_scope(ChannelApiKeyScope.MESSAGES_SEND)]

    @extend_schema(exclude=True)
    def post(self, request):
        """Accept a raw MIME message, create a Message, sign, and dispatch."""
        mailbox_id = request.META.get("HTTP_X_MAIL_FROM")
        rcpt_to_header = request.META.get("HTTP_X_RCPT_TO")

        if not mailbox_id or not rcpt_to_header:
            return Response(
                {"detail": "Missing required headers: X-Mail-From, X-Rcpt-To."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve mailbox
        try:
            mailbox = models.Mailbox.objects.select_related("domain").get(id=mailbox_id)
        except (models.Mailbox.DoesNotExist, ValueError, DjangoValidationError):
            return Response(
                {"detail": "Mailbox not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Enforce the api_key channel's resource scope. A scope_level=mailbox
        # credential can only send as that mailbox; a scope_level=maildomain
        # credential only within that domain; a global credential is
        # unrestricted. For scope_level=user we additionally require the
        # target user to have a SENDER-or-better role on the mailbox via
        # MailboxAccess — without this, a viewer-only user could mint a
        # personal api_key and submit messages.
        if not request.auth.api_key_covers(
            mailbox=mailbox, mailbox_roles=MAILBOX_ROLES_CAN_SEND
        ):
            raise PermissionDenied("API key is not authorized to send as this mailbox.")

        # Parse envelope recipients.  _create_message_from_inbound creates
        # MessageRecipient rows from MIME To/Cc headers; any address that
        # appears only in X-Rcpt-To (not in MIME headers) is added as BCC
        # after message creation — this is how SMTP BCC works.
        recipient_emails = [
            addr.strip() for addr in rcpt_to_header.split(",") if addr.strip()
        ]
        if not recipient_emails:
            return Response(
                {"detail": "X-Rcpt-To header is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_mime = request.body
        if not raw_mime:
            return Response(
                {"detail": "Empty request body."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse to validate structure
        try:
            parsed = parse_email_message(raw_mime)
        except EmailParseError:
            return Response(
                {"detail": "Failed to parse email message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate sender matches the mailbox
        sender_email = (parsed.get("from") or {}).get("email", "")
        mailbox_email = str(mailbox)
        if sender_email.lower() != mailbox_email.lower():
            return Response(
                {
                    "detail": (
                        f"From header '{sender_email}' does not match"
                        f" mailbox '{mailbox_email}'."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Create thread, contacts, message, and recipients from the parsed email.
        # is_outbound=True skips blob creation (handled by prepare_outbound_message
        # with DKIM) and AI features.
        message = _create_message_from_inbound(
            recipient_email=mailbox_email,
            parsed_email=parsed,
            raw_data=raw_mime,
            mailbox=mailbox,
            is_outbound=True,
        )
        if not message:
            return Response(
                {"detail": "Failed to create message."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Add envelope-only recipients as BCC.  _create_message_from_inbound
        # creates MessageRecipient rows from the MIME To/Cc/Bcc headers, but
        # true BCC recipients appear only in the envelope (X-Rcpt-To), never
        # in the MIME headers — that's how BCC works in SMTP.
        mime_recipients = {
            e.lower()
            for e in message.recipients.values_list("contact__email", flat=True)
        }
        for addr in recipient_emails:
            if addr.lower() not in mime_recipients:
                try:
                    contact, _ = models.Contact.objects.get_or_create(
                        email=addr,
                        mailbox=mailbox,
                        defaults={"name": addr.split("@")[0]},
                    )
                    models.MessageRecipient.objects.get_or_create(
                        message=message,
                        contact=contact,
                        type=models.MessageRecipientTypeChoices.BCC,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed to add BCC recipient (masked)")

        # Synchronous: validate recipients, throttle, DKIM sign, create blob.
        # This is a one-shot API — clean up on any failure so no orphan
        # draft remains.
        try:
            prepared = prepare_outbound_message(
                mailbox,
                message,
                "",
                "",
                raw_mime=raw_mime,
            )
        except Exception:
            message.delete()
            raise

        if not prepared:
            message.delete()
            return Response(
                {"detail": "Failed to prepare message for sending."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Dispatch async SMTP delivery
        send_message_task.delay(str(message.id))

        return Response(
            {"message_id": str(message.id), "status": "accepted"},
            status=status.HTTP_202_ACCEPTED,
        )

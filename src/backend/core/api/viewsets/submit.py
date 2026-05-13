"""Generic outbound email submission endpoint.

POST /api/v1.0/submit/
Accepts a raw RFC 5322 message and sends it from a mailbox.
Creates a Message via the inbound pipeline (with ``is_outbound=True``),
then runs ``prepare_outbound_message`` synchronously (DKIM signing, blob
creation) and dispatches SMTP delivery asynchronously via Celery.

Supports two authentication methods:
- ``ChannelApiKeyAuthentication``: API key channel with ``messages:send``
  scope. Mailbox is identified by UUID in the ``X-Mail-From`` header.
- ``ChannelJwtAuthentication``: channel session JWT via
  ``X-Channel-Token``. Mailbox is resolved from the authenticated channel.
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from core import models
from core.api.authentication import ChannelApiKeyAuthentication, ChannelJwtAuthentication
from core.enums import MAILBOX_ROLES_CAN_SEND, ChannelScope, ChannelTypes
from core.mda.inbound_create import _create_message_from_inbound
from core.mda.outbound import prepare_outbound_message
from core.mda.outbound_tasks import send_message_task
from core.mda.rfc5322 import EmailParseError, parse_email_message

logger = logging.getLogger(__name__)


class CanSubmitMessage(BasePermission):
    """Allow the request if the authenticated channel has ``messages:send``.

    Works identically for both api_key and client-bridge channels — both
    store scopes in ``channel.settings["scopes"]``.
    """

    def has_permission(self, request, view):
        channel = request.auth
        if not isinstance(channel, models.Channel):
            return False
        scopes = (channel.settings or {}).get("scopes") or []
        return ChannelScope.MESSAGES_SEND in scopes


class SubmitRawEmailView(APIView):
    """Submit a pre-composed RFC 5322 email for delivery from a mailbox.

    POST /api/v1.0/submit/
    Content-Type: message/rfc822

    **API key authentication** (``ChannelApiKeyAuthentication``):
        X-Channel-Id: <channel uuid>
        X-API-Key:    <raw secret>
        X-Mail-From:  <mailbox uuid>
        X-Rcpt-To:    <addr>[,<addr>]

    **Channel JWT authentication** (``ChannelJwtAuthentication``):
        X-Channel-Token: <JWT>
        X-Mail-From:     <email address>  (optional — defaults to channel mailbox)
        X-Rcpt-To:       <addr>[,<addr>]

    The endpoint creates a Message record, DKIM-signs the raw MIME
    synchronously, and dispatches SMTP delivery via Celery.

    Returns: ``{"message_id": "<…>", "status": "accepted"}`` (HTTP 202).
    """

    authentication_classes = [
        ChannelApiKeyAuthentication,
        ChannelJwtAuthentication,
    ]
    permission_classes = [CanSubmitMessage]

    @extend_schema(exclude=True)
    def post(self, request):
        """Accept a raw MIME message, create a Message, sign, and dispatch."""
        channel = request.auth
        is_client_bridge = isinstance(channel, models.Channel) and channel.type == ChannelTypes.CLIENT_BRIDGE

        # -- Resolve mailbox --------------------------------------------------
        if is_client_bridge:
            mailbox = channel.mailbox
        else:
            mailbox_id = request.META.get("HTTP_X_MAIL_FROM")
            if not mailbox_id:
                return Response(
                    {"detail": "Missing required headers: X-Mail-From, X-Rcpt-To."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                mailbox = models.Mailbox.objects.select_related("domain").get(
                    id=mailbox_id
                )
            except (
                models.Mailbox.DoesNotExist,
                ValueError,
                DjangoValidationError,
            ):
                return Response(
                    {"detail": "Mailbox not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Enforce api_key scope bounds (mailbox/maildomain/user level).
            if not channel.api_key_covers(
                mailbox=mailbox, mailbox_roles=MAILBOX_ROLES_CAN_SEND
            ):
                raise PermissionDenied(
                    "API key is not authorized to send as this mailbox."
                )

        # -- Validate envelope ------------------------------------------------
        rcpt_to_header = request.META.get("HTTP_X_RCPT_TO")
        if not rcpt_to_header:
            return Response(
                {"detail": "Missing required headers: X-Mail-From, X-Rcpt-To."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        # -- Parse and validate -----------------------------------------------
        try:
            parsed = parse_email_message(raw_mime)
        except EmailParseError:
            return Response(
                {"detail": "Failed to parse email message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        # -- Create message ---------------------------------------------------
        message = _create_message_from_inbound(
            recipient_email=mailbox_email,
            parsed_email=parsed,
            raw_data=raw_mime,
            mailbox=mailbox,
            channel=channel if isinstance(channel, models.Channel) else None,
            is_outbound=True,
        )
        if not message:
            return Response(
                {"detail": "Failed to create message."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # -- Envelope-only BCC recipients -------------------------------------
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

        # -- DKIM sign and prepare --------------------------------------------
        try:
            prepared = prepare_outbound_message(
                mailbox,
                message,
                "",
                "",
                user=request.user if request.user.is_authenticated else None,
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

        # -- Dispatch async SMTP delivery -------------------------------------
        send_message_task.delay(str(message.id))

        return Response(
            {"message_id": str(message.id), "status": "accepted"},
            status=status.HTTP_202_ACCEPTED,
        )

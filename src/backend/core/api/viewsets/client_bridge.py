"""API views for client-bridge authentication and message submission."""

import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from django.conf import settings

import jwt
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from core import models
from core.authentication.client_bridge import ClientBridgeChannelAuthentication
from core.enums import CLIENT_BRIDGE_ROLES_CAN_SEND
from core.mda.inbound_create import _create_message_from_inbound
from core.mda.outbound import prepare_outbound_message
from core.mda.outbound_tasks import send_message_task
from core.mda.rfc5322 import EmailParseError, parse_email_message

logger = logging.getLogger(__name__)

_INVALID_CREDENTIALS = {"detail": "Invalid credentials."}


class ClientBridgeAuthThrottle(SimpleRateThrottle):
    """Throttle auth attempts by username to prevent brute-force attacks.

    All requests come from the client-bridge service so IP-based throttling
    is useless.  We key on the username (email) from the request body instead.
    """

    scope = "client_bridge_auth"
    rate = "5/minute"

    def get_cache_key(self, request, view):
        username = request.data.get("username")
        if not username:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": username.lower(),
        }


class ClientBridgeAuthView(APIView):
    """Authenticate a client-bridge channel by email username and app-specific password.

    POST /api/v1.0/client-bridge/auth/
    Input: {"username": "<mailbox email>", "password": "..."}
    Returns: {"token": "<JWT>", "channel_id", "mailbox_id", "mailbox_email", "role",
              "expires_at": "<ISO 8601>"}

    The request must carry ``X-Service-Auth: Bearer <CLIENTBRIDGE_API_SECRET>``
    to prove it comes from the client-bridge service.

    The JWT is signed with CLIENTBRIDGE_API_SECRET and contains the channel_id,
    mailbox_id, role, and expiration. The client-bridge passes it as
    X-Channel-Token on all subsequent requests.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ClientBridgeAuthThrottle]

    def post(self, request):  # pylint: disable=missing-function-docstring
        # Validate service secret
        if not settings.FEATURE_CLIENTBRIDGE:
            return Response(
                {"detail": "Client-bridge is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )

        header = request.headers.get("X-Service-Auth", "")
        token_str = (
            header.removeprefix("Bearer ") if header.startswith("Bearer ") else header
        )
        expected = settings.CLIENTBRIDGE_API_SECRET
        if (
            not expected
            or not token_str
            or not secrets.compare_digest(token_str, expected)
        ):
            return Response(
                {"detail": "Invalid service token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        username = request.data.get("username")
        password = request.data.get("password")

        if not username or not password:
            return Response(
                {"detail": "username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse the email address
        if "@" not in username:
            return Response(
                _INVALID_CREDENTIALS,
                status=status.HTTP_401_UNAUTHORIZED,
            )
        local_part, domain_name = username.rsplit("@", 1)

        # Look up the mailbox by email address
        try:
            mailbox = models.Mailbox.objects.get(
                local_part=local_part,
                domain__name=domain_name,
            )
        except models.Mailbox.DoesNotExist:
            return Response(
                _INVALID_CREDENTIALS,
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Try all client-bridge channels on this mailbox
        channels = models.Channel.objects.filter(mailbox=mailbox, type="client-bridge")
        for channel in channels:
            stored_password = (channel.encrypted_settings or {}).get("password", "")
            if stored_password and hmac.compare_digest(stored_password, password):
                role = (channel.settings or {}).get("role", "sender")
                timeout = settings.CLIENTBRIDGE_SESSION_TIMEOUT
                expires_at = datetime.now(UTC) + timedelta(seconds=timeout)
                channel_token = jwt.encode(
                    {
                        "channel_id": str(channel.id),
                        "mailbox_id": str(mailbox.id),
                        "mailbox_email": username,
                        "role": role,
                        "exp": expires_at,
                    },
                    settings.CLIENTBRIDGE_API_SECRET,
                    algorithm="HS256",
                )
                return Response(
                    {"token": channel_token},
                    status=status.HTTP_200_OK,
                )

        return Response(
            _INVALID_CREDENTIALS,
            status=status.HTTP_401_UNAUTHORIZED,
        )


class ClientBridgeSubmitView(APIView):
    """Submit an outbound message via the client-bridge.

    POST /api/v1.0/client-bridge/submit/
    Content-Type: message/rfc822 (raw email)
    Headers: X-Channel-Token (JWT), X-Mail-From, X-Rcpt-To
    Returns: {"message_id": "...", "status": "accepted"}

    Uses ClientBridgeChannelAuthentication: the channel is resolved from
    the JWT and available as request.auth.
    """

    authentication_classes = [ClientBridgeChannelAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):  # pylint: disable=missing-function-docstring
        channel = request.auth
        mail_from = request.META.get("HTTP_X_MAIL_FROM")
        rcpt_to = request.META.get("HTTP_X_RCPT_TO")

        if not all([mail_from, rcpt_to]):
            return Response(
                {"detail": "Missing required headers: X-Mail-From, X-Rcpt-To."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Enforce channel role
        role = (channel.settings or {}).get("role", "sender")
        if role not in CLIENT_BRIDGE_ROLES_CAN_SEND:
            return Response(
                {"detail": "This channel does not have send access."},
                status=status.HTTP_403_FORBIDDEN,
            )

        mailbox = channel.mailbox
        raw_data = request.body

        # Parse the raw email
        try:
            parsed_email = parse_email_message(raw_data)
        except EmailParseError as e:
            logger.error("Client-bridge submit: failed to parse email: %s", e)
            return Response(
                {"detail": "Failed to parse email message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate sender matches the mailbox
        sender_info = parsed_email.get("from", {})
        sender_email = sender_info.get("email", "")
        mailbox_email = str(mailbox)
        if sender_email.lower() != mailbox_email.lower():
            return Response(
                {"detail": "Sender email does not match the mailbox."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Create thread, contacts, message, and recipients from the parsed email.
        # Reuses the same code path as inbound delivery, with is_outbound=True
        # to skip blob creation (handled by prepare_outbound_message with DKIM)
        # and AI features.
        message = _create_message_from_inbound(
            recipient_email=mailbox_email,
            parsed_email=parsed_email,
            raw_data=raw_data,
            mailbox=mailbox,
            channel=channel,
            is_outbound=True,
        )
        if not message:
            return Response(
                {"detail": "Failed to create message."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Go through the standard outbound pipeline:
        # validates recipients, throttles, signs DKIM, creates blob
        prepared = prepare_outbound_message(
            mailbox,
            message,
            "",
            "",
            raw_mime=raw_data,
        )
        if not prepared:
            return Response(
                {"detail": "Failed to prepare message for sending."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Dispatch async delivery
        send_message_task.delay(str(message.id))

        logger.info(
            "Message submitted via client-bridge: channel=%s, message=%s, from=%s, to=%s",
            channel.id,
            message.id,
            mail_from,
            rcpt_to,
        )

        return Response(
            {"message_id": str(message.id), "status": "accepted"},
            status=status.HTTP_202_ACCEPTED,
        )

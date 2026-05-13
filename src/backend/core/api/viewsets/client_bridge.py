"""API views for client-bridge authentication."""

import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from django.conf import settings

import jwt
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from core import models
from core.enums import ChannelTypes

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
    Returns: {"token": "<JWT>"}

    The request must carry ``X-Service-Auth: Bearer <CLIENTBRIDGE_API_SECRET>``
    to prove it comes from the client-bridge service.

    The JWT is signed with CLIENTBRIDGE_API_SECRET and contains only
    channel_id, mailbox_id, and expiration.  Scopes are always read from
    the channel's database row.  The client-bridge passes the token as
    X-Channel-Token on all subsequent requests.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ClientBridgeAuthThrottle]

    @extend_schema(exclude=True)
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
        channels = models.Channel.objects.filter(
            mailbox=mailbox, type=ChannelTypes.CLIENT_BRIDGE
        )
        for channel in channels:
            stored_password = (channel.encrypted_settings or {}).get("password", "")
            if stored_password and hmac.compare_digest(stored_password, password):
                timeout = settings.CLIENTBRIDGE_SESSION_TIMEOUT
                expires_at = datetime.now(UTC) + timedelta(seconds=timeout)
                channel_token = jwt.encode(
                    {
                        "channel_id": str(channel.id),
                        "mailbox_id": str(mailbox.id),
                        "mailbox_email": username,
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

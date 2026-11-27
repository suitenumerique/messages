"""Channel lookup for the client-bridge service (IMAP/SMTP for email clients).

The bridge calls this at IMAP/SMTP login to check the credentials and learn
the mailbox and scopes. Every other call carries the same credentials, see
``AppPasswordAuthentication``.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.authentication import AppPasswordAuthentication
from core.api.permissions import IsAuthenticated


class ClientBridgeChannelView(APIView):
    """GET /api/v1.0/client-bridge/channel/

    Headers: X-Mailbox-Email, X-App-Password
    Returns: {"channel_id", "mailbox_id", "mailbox_email", "scopes"}
    """

    authentication_classes = [AppPasswordAuthentication]
    permission_classes = [IsAuthenticated]
    app_password_scopes = {"get": None}

    @extend_schema(exclude=True)
    def get(self, request):  # pylint: disable=missing-function-docstring
        channel = request.auth
        return Response(
            {
                "channel_id": str(channel.id),
                "mailbox_id": str(channel.mailbox_id),
                "mailbox_email": str(channel.mailbox),
                "scopes": (channel.settings or {}).get("scopes", []),
            }
        )

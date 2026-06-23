"""Realtime SSE connection-token endpoint.

The browser can't send an ``Authorization`` header on an ``EventSource``, so
it first calls this authenticated endpoint to obtain a short-lived JWT, then
opens ``GET /realtime-relay/?token=<jwt>`` (Caddy reverse-proxies that path to
the relay). The token authorizes the connection's own ``user:<id>`` channel;
per-thread rooms (presence) get added to the claim here once that feature lands.
"""

from django.conf import settings

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, views
from rest_framework.response import Response

from core.services import realtime


class RealtimeTokenView(views.APIView):
    """Mint a short-lived SSE connection token for the current user."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=["realtime"],
        request=None,
        responses={
            200: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {
                        "token": {
                            "type": "string",
                            "nullable": True,
                            "description": (
                                "SSE connection JWT, or null when realtime is "
                                "disabled (client should poll instead)."
                            ),
                        }
                    },
                    "required": ["token"],
                },
                description="A short-lived connection token (or null when disabled).",
            ),
            503: OpenApiResponse(
                description="Realtime is enabled but misconfigured (no signing secret)."
            ),
        },
    )
    def post(self, request):
        # Realtime off is a normal answer, not an error: return a 200 with a null
        # token so the client cleanly falls back to polling (and can re-check
        # later) instead of treating it as a failure to retry. This is also the
        # graceful load-shed lever — flipping REALTIME_ENABLED off restarts the
        # dyno (dropping live streams), and every reconnect then lands here.
        if not settings.REALTIME_ENABLED:
            return Response({"token": None})
        try:
            token = realtime.mint_connection_token(str(request.user.id))
        except realtime.RealtimeMisconfigured:
            # Enabled but no signing secret set: a real server misconfiguration
            # (not a normal "off"), so surface it as an error the client retries.
            return Response({"detail": "realtime unavailable"}, status=503)
        return Response({"token": token})

"""API ViewSet for entitlements."""

import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.entitlements import (
    EntitlementsUnavailableError,
    get_mailbox_entitlements,
    get_user_entitlements,
)
from core.models import Mailbox

logger = logging.getLogger(__name__)


class EntitlementsView(APIView):
    """API endpoint for retrieving user and mailbox entitlements.

    GET /api/v1.0/entitlements/
        Returns user entitlements from cache.

    GET /api/v1.0/entitlements/?mailbox_id=<uuid>
        Also fetches mailbox entitlements on-demand.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return entitlements for the authenticated user."""
        try:
            user_entitlements = get_user_entitlements(
                request.user.sub, request.user.email
            )
        except EntitlementsUnavailableError:
            return Response(
                {"detail": "Entitlements service unavailable"}, status=503
            )

        response_data = {
            "can_access": user_entitlements.get("can_access", False),
            "can_admin_maildomains": user_entitlements.get(
                "can_admin_maildomains", []
            ),
            "operator": user_entitlements.get("operator"),
            "mailbox": None,
        }

        mailbox_id = request.query_params.get("mailbox_id")
        if mailbox_id:
            try:
                mailbox = Mailbox.objects.select_related("domain").get(id=mailbox_id)
                mailbox_email = str(mailbox)
                mailbox_data = get_mailbox_entitlements(mailbox_email)
                response_data["mailbox"] = {
                    "max_storage": mailbox_data.get("max_storage"),
                    "storage_used": mailbox_data.get("storage_used"),
                }
            except Mailbox.DoesNotExist:
                response_data["mailbox"] = None
            except EntitlementsUnavailableError:
                return Response(
                    {"detail": "Entitlements service unavailable"}, status=503
                )

        return Response(response_data)

"""Middleware to enforce entitlements-based access control on API requests."""

import logging
import re

from django.conf import settings
from django.http import JsonResponse

from core.entitlements import (
    EntitlementsUnavailableError,
    get_user_entitlements,
)

logger = logging.getLogger(__name__)

# Paths that should be excluded from entitlements checks
EXCLUDED_PATH_PATTERNS = [
    re.compile(rf"^/api/{settings.API_VERSION}/config/"),
    re.compile(rf"^/api/{settings.API_VERSION}/inbound/mta/"),
    re.compile(rf"^/api/{settings.API_VERSION}/mta/"),
    re.compile(rf"^/api/{settings.API_VERSION}/metrics/"),
    re.compile(rf"^/api/{settings.API_VERSION}/entitlements/"),
    re.compile(rf"^/api/{settings.API_VERSION}/prometheus/"),
]


class EntitlementsMiddleware:
    """Middleware that checks can_access entitlement for authenticated API requests.

    - Skips non-API paths
    - Skips excluded paths (config, MTA, metrics, entitlements, prometheus)
    - Skips unauthenticated requests and superusers
    - Returns 403 if can_access is False
    - Returns 503 if entitlements service is unavailable (fail closed)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only check API paths
        if not request.path.startswith(f"/api/{settings.API_VERSION}/"):
            return self.get_response(request)

        # Skip excluded paths
        for pattern in EXCLUDED_PATH_PATTERNS:
            if pattern.match(request.path):
                return self.get_response(request)

        # Skip unauthenticated requests (they'll get 401 from DRF)
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return self.get_response(request)

        # Skip superusers
        if request.user.is_superuser:
            return self.get_response(request)

        try:
            entitlements = get_user_entitlements(
                request.user.sub, request.user.email
            )
        except EntitlementsUnavailableError:
            logger.warning(
                "Entitlements service unavailable for user %s",
                request.user.sub,
            )
            return JsonResponse(
                {"detail": "Entitlements service unavailable"}, status=503
            )

        if not entitlements.get("can_access", False):
            return JsonResponse(
                {"detail": "Access denied by entitlements policy"}, status=403
            )

        return self.get_response(request)

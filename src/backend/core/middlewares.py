"""Custom Django middlewares"""

from django.conf import settings
from django.http import HttpResponse


class AuthMiddleware:
    """
    Middleware to enforce authentication via Bearer token for metrics endpoints.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        normalized_path = request.path.rstrip("/")
        if normalized_path.endswith("/prometheus/metrics"):
            if (
                settings.PROMETHEUS_API_KEY
                and request.headers.get("Authorization")
                != f"Bearer {settings.PROMETHEUS_API_KEY}"
            ):
                return HttpResponse("Unauthorized", status=401)
        elif normalized_path.endswith("/maildomain_users/metrics"):
            if (
                settings.MAILDOMAIN_USER_METRICS_API_KEY
                and request.headers.get("Authorization")
                != f"Bearer {settings.MAILDOMAIN_USER_METRICS_API_KEY}"
            ):
                return HttpResponse("Unauthorized", status=401)

        return self.get_response(request)

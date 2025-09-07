"""Custom Django middlewares"""

from secrets import compare_digest

from django.conf import settings
from django.http import HttpResponse


class PrometheusAuthMiddleware:
    """
    Middleware to enforce authentication via Bearer token for Prometheus metrics endpoint.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(f"/api/{settings.API_VERSION}/prometheus"):
            if settings.PROMETHEUS_API_KEY:
                if not compare_digest(
                    request.headers.get("Authorization") or "",
                    f"Bearer {settings.PROMETHEUS_API_KEY}",
                ):
                    return HttpResponse("Unauthorized", status=401)

        return self.get_response(request)

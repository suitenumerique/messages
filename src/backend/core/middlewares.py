"""Custom Django middlewares"""

from django.conf import settings
from django.http import HttpResponse


class PrometheusAuthMiddleware:
    """
    Middleware to enforce authentication via Bearer token for Prometheus metrics endpoint.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.endswith("/prometheus/metrics"):
            if (
                settings.PROMETHEUS_API_KEY
                and request.headers.get("Authorization")
                != f"Bearer {settings.PROMETHEUS_API_KEY}"
            ):
                return HttpResponse("Unauthorized", status=401)

        return self.get_response(request)

"""Custom Django middlewares"""

from secrets import compare_digest

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse


class AuthMiddleware:
    """
    Middleware to enforce authentication via Bearer token for metrics endpoints.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _paths_match(self, path1: str, path2: str) -> bool:
        return path1.rstrip("/") == path2.rstrip("/")

    def __call__(self, request):
        if self._paths_match(request.path, reverse("prometheus-django-metrics")):
            if settings.PROMETHEUS_API_KEY:
                if not compare_digest(
                    request.headers.get("Authorization") or "",
                    f"Bearer {settings.PROMETHEUS_API_KEY}",
                ):
                    return HttpResponse("Unauthorized", status=401)
        elif self._paths_match(request.path, reverse("maildomain-users-metrics")):
            if settings.MAILDOMAIN_USER_METRICS_API_KEY:
                if not compare_digest(
                    request.headers.get("Authorization") or "",
                    f"Bearer {settings.MAILDOMAIN_USER_METRICS_API_KEY}",
                ):
                    return HttpResponse("Unauthorized", status=401)

        return self.get_response(request)

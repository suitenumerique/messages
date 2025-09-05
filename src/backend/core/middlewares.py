"""Custom Django middlewares"""

from secrets import compare_digest

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse, NoReverseMatch


class AuthMiddleware:
    """
    Middleware to enforce authentication via Bearer token for metrics endpoints.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Resolve once; endpoints may be disabled by feature flags
        try:
            self._prom_path = reverse("prometheus-django-metrics")
        except NoReverseMatch:
            self._prom_path = None
        try:
            self._maildomain_path = reverse("maildomain-users-metrics")
        except NoReverseMatch:
            self._maildomain_path = None

    def _paths_match(self, path1: str, path2: str) -> bool:
        return path1.rstrip("/") == path2.rstrip("/")

    def __call__(self, request):
        if self._prom_path and self._paths_match(request.path, self._prom_path):
            if settings.PROMETHEUS_API_KEY:
                if not compare_digest(
                    request.headers.get("Authorization") or "",
                    f"Bearer {settings.PROMETHEUS_API_KEY}",
                ):
                    return HttpResponse("Unauthorized", status=401)
        elif self._maildomain_path and self._paths_match(request.path, self._maildomain_path):
            if settings.MAILDOMAIN_USER_METRICS_API_KEY:
                if not compare_digest(
                    request.headers.get("Authorization") or "",
                    f"Bearer {settings.MAILDOMAIN_USER_METRICS_API_KEY}",
                ):
                    return HttpResponse("Unauthorized", status=401)

        return self.get_response(request)

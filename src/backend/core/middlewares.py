from django.conf import settings
from django.http import HttpResponse


class PrometheusAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.endswith("/prometheus/metrics"):
            expected_api_key = f"Bearer {settings.PROMETHEUS_API_KEY}"
            if (
                settings.PROMETHEUS_API_KEY
                and request.headers.get("Authorization") != expected_api_key
            ):
                return HttpResponse("Unauthorized", status=401)

        return self.get_response(request)

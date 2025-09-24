"""
Custom middleware for the messages application.
"""

from secrets import compare_digest

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

from corsheaders.middleware import CorsMiddleware


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


class CustomCorsMiddleware(CorsMiddleware):
    """
    Custom CORS middleware that allows all origins for specific API paths.

    This middleware extends the default CORS middleware to allow all origins
    for paths matching /api/{version}/inbound/widget/* while maintaining the
    existing CORS configuration for all other paths.
    """

    def _get_cors_headers_for_widget_api(self, request):
        """
        Get CORS headers for widget API requests - allows all origins, headers, and methods.

        Args:
            request: The Django request object

        Returns:
            dict: CORS headers for widget API
        """
        origin = request.META.get("HTTP_ORIGIN", "*")

        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "false",
            "Vary": "Origin, Content-Type, X-Channel-ID",
        }

        # Add preflight headers for OPTIONS requests
        if request.method == "OPTIONS":
            headers.update(
                {
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-Channel-ID",
                    "Access-Control-Max-Age": "86400",  # 24 hours
                }
            )

        return headers

    def __call__(self, request):
        """
        Process the request and response with custom CORS handling for widget API.
        """

        def update_headers(response, headers):
            """Update the response headers with the given headers."""
            for header, value in headers.items():
                if header.lower() == "vary":
                    patch_vary_headers(response, [h.strip() for h in value.split(",")])
                else:
                    response[header] = value

        if request.path.startswith(f"/api/{settings.API_VERSION}/inbound/widget/"):
            # Handle CORS for widget API requests manually
            if request.method == "OPTIONS":
                # Handle preflight requests
                headers = self._get_cors_headers_for_widget_api(request)
                response = HttpResponse(status=200)
                update_headers(response, headers)
                return response

            # Process the request normally
            response = self.get_response(request)

            # Add CORS headers to the response
            headers = self._get_cors_headers_for_widget_api(request)
            update_headers(response, headers)

            return response

        # Use default CORS behavior for all other paths
        return super().__call__(request)


class XForwardedForMiddleware:
    """
    Middleware that sets the REMOTE_ADDR from the X-Forwarded-For header if present.

    Note: This middleware is only enabled if USE_X_FORWARDED_FOR is True (default is False), because
    it's not safe to use in production if the headers are not trusted (safely overridden by a proxy).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            real_ip = request.META["HTTP_X_FORWARDED_FOR"]
        except KeyError:
            pass
        else:
            # HTTP_X_FORWARDED_FOR can be a comma-separated list of IPs. The
            # client's IP will be the first one.
            real_ip = real_ip.split(",")[0].strip()
            request.META["REMOTE_ADDR"] = real_ip

        return self.get_response(request)

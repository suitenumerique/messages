"""URL configuration for the messages project"""

from logging import getLogger

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.db import connection
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.urls import include, path, re_path

from drf_spectacular.views import (
    SpectacularJSONAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

logger = getLogger(__name__)


def heartbeat(request):
    """Healthcheck endpoint to verify that the application is running and DB is reachable."""
    try:
        connection.ensure_connection()
        return HttpResponse("OK")
    except OperationalError as e:
        logger.error("Database error: %s", e)
        return HttpResponse("DB Unavailable", status=500)


urlpatterns = [
    path(f"{settings.ADMIN_URL}/", admin.site.urls),
    path("", include("core.urls")),
    path(
        "__heartbeat__/",
        heartbeat,
        name="healthcheck",
    ),
]

if settings.DEBUG:
    urlpatterns = (
        urlpatterns
        + staticfiles_urlpatterns()
        + static(settings.MEDIA_URL, item_root=settings.MEDIA_ROOT)
    )


if settings.USE_SWAGGER or settings.DEBUG:
    urlpatterns += [
        path(
            f"api/{settings.API_VERSION}/swagger.json",
            SpectacularJSONAPIView.as_view(
                api_version=settings.API_VERSION,
                urlconf="core.urls",
            ),
            name="client-api-schema",
        ),
        path(
            f"api/{settings.API_VERSION}/swagger/",
            SpectacularSwaggerView.as_view(url_name="client-api-schema"),
            name="swagger-ui-schema",
        ),
        re_path(
            f"api/{settings.API_VERSION}/redoc/",
            SpectacularRedocView.as_view(url_name="client-api-schema"),
            name="redoc-schema",
        ),
    ]

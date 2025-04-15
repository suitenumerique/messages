"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path, re_path

from rest_framework.routers import DefaultRouter

from core.api import viewsets
from core.authentication.urls import urlpatterns as oidc_urls
from core.api.viewset_mta import MTAViewSet

# - Main endpoints
router = DefaultRouter()
router.register("mta", MTAViewSet, basename="mta")


urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                *router.urls,
                *oidc_urls
            ]
        ),
    ),
    path(f"api/{settings.API_VERSION}/config/", viewsets.ConfigView.as_view()),
]

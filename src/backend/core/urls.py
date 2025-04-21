"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from core.api import viewsets
from core.api.viewset_mta import MTAViewSet
from core.authentication.urls import urlpatterns as oidc_urls

# - Main endpoints
router = DefaultRouter()
router.register("mta", MTAViewSet, basename="mta")
router.register("users", viewsets.UserViewSet, basename="users")
router.register("mailboxes", viewsets.MailboxViewSet, basename="mailboxes")
router.register("threads", viewsets.ThreadViewSet, basename="threads")
router.register("messages", viewsets.MessageViewSet, basename="messages")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include([*router.urls, *oidc_urls]),
    ),
    path(f"api/{settings.API_VERSION}/config/", viewsets.ConfigView.as_view()),
    path(
        f"api/{settings.API_VERSION}/message-create/",
        viewsets.MessageCreateView.as_view(),
    ),
    path(
        f"api/{settings.API_VERSION}/read/",
        viewsets.ChangeReadStatusViewSet.as_view(),
        name="change-read-status",
    ),
]

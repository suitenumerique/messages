"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from core.api.viewsets.config import ConfigView
from core.api.viewsets.draft import DraftMessageView
from core.api.viewsets.flag import ChangeFlagViewSet
from core.api.viewsets.mailbox import MailboxViewSet
from core.api.viewsets.message import MessageViewSet
from core.api.viewsets.mta import MTAViewSet
from core.api.viewsets.send import SendMessageView
from core.api.viewsets.thread import ThreadViewSet
from core.api.viewsets.user import UserViewSet
from core.authentication.urls import urlpatterns as oidc_urls

# - Main endpoints
router = DefaultRouter()
router.register("mta", MTAViewSet, basename="mta")
router.register("users", UserViewSet, basename="users")
router.register("mailboxes", MailboxViewSet, basename="mailboxes")
router.register("threads", ThreadViewSet, basename="threads")
router.register("messages", MessageViewSet, basename="messages")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include([*router.urls, *oidc_urls]),
    ),
    path(f"api/{settings.API_VERSION}/config/", ConfigView.as_view()),
    path(
        f"api/{settings.API_VERSION}/flag/",
        ChangeFlagViewSet.as_view(),
        name="change-flag",
    ),
    path(
        f"api/{settings.API_VERSION}/draft/",
        DraftMessageView.as_view(),
        name="draft-message",
    ),
    path(
        f"api/{settings.API_VERSION}/draft/<uuid:message_id>/",
        DraftMessageView.as_view(),
        name="draft-message-detail",
    ),
    path(
        f"api/{settings.API_VERSION}/send/",
        SendMessageView.as_view(),
        name="send-message",
    ),
]

"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path
from django.shortcuts import redirect
from django.http import HttpResponse

from rest_framework.routers import DefaultRouter

from core.api.viewsets.blob import BlobViewSet
from core.api.viewsets.config import ConfigView
from core.api.viewsets.contacts import ContactViewSet
from core.api.viewsets.draft import DraftMessageView
from core.api.viewsets.flag import ChangeFlagViewSet
from core.api.viewsets.import_message import ImportViewSet
from core.api.viewsets.label import LabelViewSet
from core.api.viewsets.mailbox import MailboxViewSet
from core.api.viewsets.mailbox_access import MailboxAccessViewSet

# Import the viewsets from the correctly named file
from core.api.viewsets.maildomain import (
    AdminMailDomainMailboxViewSet,
    AdminMailDomainUserViewSet,
    AdminMailDomainViewSet,
)
from core.api.viewsets.message import MessageViewSet
from core.api.viewsets.mta import MTAViewSet
from core.api.viewsets.send import SendMessageView
from core.api.viewsets.task import TaskDetailView
from core.api.viewsets.thread import ThreadViewSet
from core.api.viewsets.thread_access import ThreadAccessViewSet
from core.api.viewsets.user import UserViewSet
from core.authentication.urls import urlpatterns as oidc_urls

def api_root(request):
    """Simple API root view showing available endpoints."""
    return HttpResponse("""
    <html>
    <head><title>Messages API</title></head>
    <body>
        <h1>Messages API</h1>
        <h2>Available Endpoints:</h2>
        <ul>
            <li><a href="/api/v1.0/">API Root</a></li>
            <li><a href="/api/v1.0/deep_search/status/">Deep Search Status</a></li>
            <li><a href="/v1.0/swagger/">API Documentation (Swagger)</a></li>
            <li><a href="/v1.0/redoc/">API Documentation (ReDoc)</a></li>
        </ul>
        <h3>Deep Search Endpoints:</h3>
        <ul>
            <li><strong>POST</strong> /api/v1.0/deep_search/intelligent-search/ - AI-powered email search</li>
            <li><strong>POST</strong> /api/v1.0/deep_search/conversation/ - Chatbot conversation</li>
            <li><strong>GET</strong> /api/v1.0/deep_search/status/ - Service status</li>
        </ul>
        <p><em>Note: Most endpoints require authentication.</em></p>
    </body>
    </html>
    """, content_type="text/html")

# - Main endpoints
router = DefaultRouter()
router.register("mta", MTAViewSet, basename="mta")
router.register("users", UserViewSet, basename="users")
router.register("messages", MessageViewSet, basename="messages")
router.register("blob", BlobViewSet, basename="blob")
router.register("contacts", ContactViewSet, basename="contacts")
router.register("threads", ThreadViewSet, basename="threads")
router.register("labels", LabelViewSet, basename="labels")
router.register("mailboxes", MailboxViewSet, basename="mailboxes")
router.register("maildomains", AdminMailDomainViewSet, basename="admin-maildomains")

# Router for /threads/{thread_id}/accesses/
thread_access_nested_router = DefaultRouter()
thread_access_nested_router.register(
    r"accesses", ThreadAccessViewSet, basename="thread-access"
)

# Router for /mailboxes/{mailbox_id}/accesses/
mailbox_access_nested_router = DefaultRouter()
mailbox_access_nested_router.register(
    r"accesses", MailboxAccessViewSet, basename="mailboxaccess"
)

# Router for /maildomains/{maildomain_id}/mailboxes/
mailbox_management_nested_router = DefaultRouter()
mailbox_management_nested_router.register(
    r"mailboxes",
    AdminMailDomainMailboxViewSet,
    basename="admin-maildomains-mailbox",
)
mailbox_management_nested_router.register(
    r"users",
    AdminMailDomainUserViewSet,
    basename="admin-maildomains-user",
)

urlpatterns = [
    # Root URL for API discovery
    path("", api_root, name="api-root"),
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                *router.urls,  # Includes mta, users, messages, blob, ... (top-level)
                path(
                    "threads/<uuid:thread_id>/",
                    include(
                        thread_access_nested_router.urls
                    ),  # Includes /threads/{id}/accesses/
                ),
                path(
                    "mailboxes/<uuid:mailbox_id>/",
                    include(
                        mailbox_access_nested_router.urls
                    ),  # Includes /mailboxes/{id}/accesses/
                ),
                path(
                    "maildomains/<uuid:maildomain_pk>/",
                    include(
                        mailbox_management_nested_router.urls
                    ),  # Includes /maildomains/{id}/mailboxes/, # Includes /maildomains/{id}/users/
                ),
                # Deep search endpoints for AI-powered search
                path("deep_search/", include("deep_search.urls")),
                *oidc_urls,
            ]
        ),
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
    path(
        f"api/{settings.API_VERSION}/tasks/<str:task_id>/",
        TaskDetailView.as_view(),
        name="task-detail",
    ),
    path(
        f"api/{settings.API_VERSION}/import/file/",
        ImportViewSet.as_view({"post": "import_file"}),
        name="import-file",
    ),
    path(
        f"api/{settings.API_VERSION}/import/imap/",
        ImportViewSet.as_view({"post": "import_imap"}),
        name="import-imap",
    ),
]

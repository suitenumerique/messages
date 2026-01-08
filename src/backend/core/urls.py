"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from core.api.viewsets.blob import BlobViewSet
from core.api.viewsets.config import ConfigView
from core.api.viewsets.contacts import ContactViewSet
from core.api.viewsets.draft import DraftMessageView
from core.api.viewsets.drive import DriveAPIView
from core.api.viewsets.flag import ChangeFlagView
from core.api.viewsets.image_proxy import ImageProxyViewSet
from core.api.viewsets.import_message import ImportViewSet, MessagesArchiveUploadViewSet
from core.api.viewsets.inbound.mta import InboundMTAViewSet
from core.api.viewsets.inbound.widget import InboundWidgetViewSet
from core.api.viewsets.label import LabelViewSet
from core.api.viewsets.mailbox import MailboxViewSet
from core.api.viewsets.mailbox_access import MailboxAccessViewSet

# Import the viewsets from the correctly named file
from core.api.viewsets.maildomain import (
    AdminMailDomainMailboxViewSet,
    AdminMailDomainMessageTemplateViewSet,
    AdminMailDomainViewSet,
)
from core.api.viewsets.maildomain_access import MaildomainAccessViewSet
from core.api.viewsets.message import MessageViewSet
from core.api.viewsets.message_template import (
    AvailableMailboxMessageTemplateViewSet,
    MailboxMessageTemplateViewSet,
)
from core.api.viewsets.metrics import MailDomainUsersMetricsApiView
from core.api.viewsets.placeholder import PlaceholderView
from core.api.viewsets.send import SendMessageView
from core.api.viewsets.task import TaskDetailView
from core.api.viewsets.thread import ThreadViewSet
from core.api.viewsets.thread_access import ThreadAccessViewSet
from core.api.viewsets.user import UserViewSet
from core.authentication.urls import urlpatterns as oidc_urls

# - Main endpoints
router = DefaultRouter()
router.register("users", UserViewSet, basename="users")
router.register("messages", MessageViewSet, basename="messages")
router.register("blob", BlobViewSet, basename="blob")
router.register("contacts", ContactViewSet, basename="contacts")
router.register("threads", ThreadViewSet, basename="threads")
router.register("labels", LabelViewSet, basename="labels")
router.register("mailboxes", MailboxViewSet, basename="mailboxes")
router.register("maildomains", AdminMailDomainViewSet, basename="admin-maildomains")
router.register(
    "import/file/upload",
    MessagesArchiveUploadViewSet,
    basename="messages-archive-upload",
)

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

# Router for /mailboxes/{mailbox_id}/image-proxy/
mailbox_image_proxy_nested_router = DefaultRouter()
mailbox_image_proxy_nested_router.register(
    r"image-proxy", ImageProxyViewSet, basename="image-proxy"
)

# Router for /maildomains/{maildomain_pk}/**/
maildomain_nested_router = DefaultRouter()
# Register /maildomains/{maildomain_pk}/mailboxes/
maildomain_nested_router.register(
    r"mailboxes",
    AdminMailDomainMailboxViewSet,
    basename="admin-maildomains-mailbox",
)
# Register /maildomains/{maildomain_pk}/accesses/
maildomain_nested_router.register(
    r"accesses", MaildomainAccessViewSet, basename="admin-maildomains-access"
)

# Router for /inbound/
inbound_nested_router = DefaultRouter()
inbound_nested_router.register(r"mta", InboundMTAViewSet, basename="inbound-mta")
inbound_nested_router.register(
    r"widget", InboundWidgetViewSet, basename="inbound-widget"
)


# Router for /maildomains/{maildomain_id}/message-templates/
# allow to manage message templates for a maildomain in admin view
maildomain_message_template_nested_router = DefaultRouter()
maildomain_message_template_nested_router.register(
    r"message-templates",
    AdminMailDomainMessageTemplateViewSet,
    basename="admin-maildomains-message-templates",
)

# Router for /mailboxes/{mailbox_id}/message-templates/available/
# allow to insert the template into editor (message, signature)
mailbox_message_template_nested_router = DefaultRouter()
mailbox_message_template_nested_router.register(
    r"message-templates/available",
    AvailableMailboxMessageTemplateViewSet,
    basename="available-mailbox-message-templates",
)
mailbox_message_template_nested_router.register(
    r"message-templates",
    MailboxMessageTemplateViewSet,
    basename="mailbox-message-templates",
)

urlpatterns = [
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
                    "mailboxes/<uuid:mailbox_id>/",
                    include(
                        mailbox_image_proxy_nested_router.urls
                    ),  # Includes /mailboxes/{id}/image-proxy/
                ),
                path(
                    "mailboxes/<uuid:mailbox_id>/",
                    include(
                        mailbox_message_template_nested_router.urls
                    ),  # Includes /mailboxes/{id}/message-templates/
                ),
                path(
                    "maildomains/<uuid:maildomain_pk>/",
                    include(maildomain_nested_router.urls),
                ),
                path(
                    "inbound/",
                    include(inbound_nested_router.urls),
                ),
                path(
                    "maildomains/<uuid:maildomain_pk>/",
                    include(
                        maildomain_message_template_nested_router.urls
                    ),  # Includes /maildomains/{id}/message-templates/
                ),
                *oidc_urls,
            ]
        ),
    ),
    path(f"api/{settings.API_VERSION}/config/", ConfigView.as_view()),
    path(
        f"api/{settings.API_VERSION}/flag/",
        ChangeFlagView.as_view(),
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
    path(
        f"api/{settings.API_VERSION}/placeholders/",
        PlaceholderView.as_view(),
        name="placeholders",
    ),
    path(
        f"api/{settings.API_VERSION}/metrics/maildomain_users/",
        MailDomainUsersMetricsApiView.as_view(),
        name="maildomain-users-metrics",
    ),
    # Alias for MTA check endpoint
    path(
        f"api/{settings.API_VERSION}/mta/check-recipients/",
        InboundMTAViewSet.as_view({"post": "check"}),
        name="mta-check-recipients",
    ),
    # Alias for MTA deliver endpoint
    path(
        f"api/{settings.API_VERSION}/mta/inbound-email/",
        InboundMTAViewSet.as_view({"post": "deliver"}),
        name="mta-inbound-email",
    ),
]

if settings.DRIVE_CONFIG.get("base_url"):
    urlpatterns += [
        path(
            f"api/{settings.API_VERSION}/third-party/drive/",
            DriveAPIView.as_view(),
            name="drive",
        ),
    ]

if settings.ENABLE_PROMETHEUS:
    urlpatterns += [
        path(
            f"api/{settings.API_VERSION}/prometheus/",
            include("django_prometheus.urls"),
            name="prometheus-metrics",
        ),
    ]

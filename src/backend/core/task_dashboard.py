"""The background-task queue dashboard, mounted inside the Django admin.

Shows live broker state — per-queue backlog and throughput, what each worker is
holding, delayed messages, and the dead-letter queues, with requeue/purge
actions. It replaces the Flower UI the Celery setup ran as a separate service:
no extra process, no second port, and no second thing to put behind auth.

The dashboard ships as a WSGI app, so this module adapts it to Django and, more
importantly, puts it behind the admin's own access control. It exposes
destructive endpoints (flush a queue, purge a DLQ) and renders task payloads,
and performs no authentication of its own.
"""

import io
import logging
import re
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# Mounted under the admin so it inherits ``admin_view``'s staff check and the
# admin's login redirect.
DASHBOARD_PATH = f"{settings.ADMIN_URL}/tasks/"


def _is_same_origin(request):
    """Whether a state-changing request came from our own pages.

    The dashboard's buttons POST from JavaScript that knows nothing about
    Django, so it can't carry a CSRF token and the view has to be exempt. This
    is the replacement guarantee: a cross-site page can issue a POST, but it
    cannot forge ``Origin``/``Referer``, so requiring them to match this host
    blocks exactly what CSRF protection blocks.
    """
    origin = request.META.get("HTTP_ORIGIN")
    if origin is None:
        referer = request.META.get("HTTP_REFERER")
        if not referer:
            return False
        origin = referer

    parsed = urlparse(origin)
    if not parsed.netloc:
        return False
    return parsed.netloc == request.get_host()


def _build_environ(request):
    """A WSGI environ the dashboard app can serve, from a Django request."""
    environ = request.META.copy()
    environ["PATH_INFO"] = request.path
    # Django has already consumed the stream to populate ``request.body``;
    # hand the app a fresh reader over the same bytes.
    environ["wsgi.input"] = io.BytesIO(request.body)
    environ["CONTENT_LENGTH"] = str(len(request.body))
    return environ


def _make_view(wsgi_app):
    """Adapt the dashboard's WSGI app into a Django view."""

    def view(request, path=""):  # pylint: disable=unused-argument
        if request.method not in ("GET", "HEAD") and not _is_same_origin(request):
            return HttpResponseForbidden("Cross-origin request rejected.")

        status_line = []
        headers = []

        def start_response(status, response_headers, exc_info=None):
            # pylint: disable=unused-argument
            status_line.append(status)
            headers.extend(response_headers)

        body = b"".join(wsgi_app(_build_environ(request), start_response))

        response = HttpResponse(body, status=int(status_line[0].split(" ", 1)[0]))
        for header, value in headers:
            response[header] = value
        return response

    # Exempt from CSRF (see ``_is_same_origin``) but *not* from authentication:
    # ``admin_view`` requires an active staff session and adds never_cache.
    return admin.site.admin_view(csrf_exempt(view))


def get_dashboard_urlpatterns():
    """URL patterns for the dashboard, or ``[]`` when the broker has none.

    The dashboard reads the broker's Redis keyspace directly, so it only exists
    for the Streams broker — under the eager/in-process broker used by tests
    and the no-Redis dev profile there is nothing to show, and the admin index
    link disappears with it.
    """
    # pylint: disable=import-outside-toplevel
    import dramatiq

    try:
        from dramatiq_redis_streams import StreamsBroker
        from dramatiq_redis_streams.dashboard import DashboardApp
    except ImportError:  # pragma: no cover - the package is a hard dependency
        logger.warning("dramatiq_redis_streams is not installed; dashboard disabled")
        return []

    # This runs while the root URLConf is being imported, so anything raising
    # here takes the whole site down — including ``get_broker()``, which builds
    # a default broker (and can fail on its dependencies) when none is set.
    try:
        broker = dramatiq.get_broker()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not resolve the task broker; dashboard disabled")
        return []

    if not isinstance(broker, StreamsBroker):
        return []

    view = _make_view(DashboardApp(broker, prefix=DASHBOARD_PATH))
    pattern = rf"^{re.escape(DASHBOARD_PATH)}(?P<path>.*)$"
    return [re_path(pattern, view, name="task-dashboard")]

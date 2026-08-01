"""Tests for the queue dashboard mounted in the Django admin.

The dashboard ships as an unauthenticated WSGI app with destructive endpoints
(flush a queue, purge a dead-letter queue) and renders task payloads. Every
test here is about the wrapper that makes mounting it safe.
"""

from unittest import mock

from django.conf import settings
from django.test import RequestFactory

import pytest

from core import task_dashboard


class TestMounting:
    """Where the dashboard is exposed — and where it deliberately is not."""

    def test_not_mounted_without_the_streams_broker(self):
        """Tests and the no-Redis dev profile run an in-process broker.

        There is no Redis keyspace to read, so the dashboard would only 500 —
        and the admin index link disappears with the URL.
        """
        assert task_dashboard.get_dashboard_urlpatterns() == []

    def test_mounted_under_the_admin_when_the_broker_supports_it(self):
        from dramatiq_redis_streams import (  # pylint: disable=import-outside-toplevel
            StreamsBroker,
        )

        # Constructing the broker opens no connection.
        broker = StreamsBroker(url="redis://127.0.0.1:6379/15")

        with mock.patch("dramatiq.get_broker", return_value=broker):
            patterns = task_dashboard.get_dashboard_urlpatterns()

        assert len(patterns) == 1
        assert patterns[0].name == "task-dashboard"
        # Under the admin prefix, so ``admin_view`` gates it and the admin
        # login redirect applies.
        assert patterns[0].pattern.match(f"{settings.ADMIN_URL}/tasks/") is not None


class TestSameOriginGuard:
    """The dashboard's buttons POST without a CSRF token, so the view is
    exempt; this is the guarantee that replaces it."""

    @staticmethod
    def _post(**headers):
        return RequestFactory().post("/admin/tasks/api/queues/default/flush", **headers)

    def test_matching_origin_is_accepted(self):
        request = self._post(HTTP_ORIGIN="http://testserver")

        assert task_dashboard._is_same_origin(request) is True  # pylint: disable=protected-access

    def test_foreign_origin_is_rejected(self):
        request = self._post(HTTP_ORIGIN="https://evil.example.com")

        assert task_dashboard._is_same_origin(request) is False  # pylint: disable=protected-access

    def test_referer_is_used_when_origin_is_absent(self):
        request = self._post(HTTP_REFERER="http://testserver/admin/tasks/")

        assert task_dashboard._is_same_origin(request) is True  # pylint: disable=protected-access

    def test_no_origin_and_no_referer_is_rejected(self):
        """A request that proves nothing about where it came from gets nothing."""
        assert task_dashboard._is_same_origin(self._post()) is False  # pylint: disable=protected-access


@pytest.mark.django_db
class TestView:
    """The Django adapter around the WSGI app."""

    @staticmethod
    def _view(wsgi_app):
        return task_dashboard._make_view(wsgi_app)  # pylint: disable=protected-access

    def test_cross_origin_post_never_reaches_the_dashboard(self):
        """A forged POST must not flush a queue — the app is not even called."""
        wsgi_app = mock.Mock()
        request = RequestFactory().post(
            "/admin/tasks/api/queues/default/flush",
            HTTP_ORIGIN="https://evil.example.com",
        )
        request.user = mock.Mock(is_active=True, is_staff=True)

        response = self._view(wsgi_app)(request)

        assert response.status_code == 403
        wsgi_app.assert_not_called()

    def test_anonymous_users_are_sent_to_the_admin_login(self):
        from django.contrib.auth.models import (  # pylint: disable=import-outside-toplevel
            AnonymousUser,
        )

        wsgi_app = mock.Mock()
        request = RequestFactory().get("/admin/tasks/")
        request.user = AnonymousUser()

        response = self._view(wsgi_app)(request)

        assert response.status_code == 302
        assert "login" in response["Location"]
        wsgi_app.assert_not_called()

    def test_a_staff_get_is_proxied_through_with_status_and_headers(self):
        def wsgi_app(environ, start_response):
            assert environ["PATH_INFO"] == "/admin/tasks/api/overview"
            start_response("200 OK", [("Content-Type", "application/json")])
            return [b'{"queues": []}']

        request = RequestFactory().get("/admin/tasks/api/overview")
        request.user = mock.Mock(is_active=True, is_staff=True)

        response = self._view(wsgi_app)(request)

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert response.content == b'{"queues": []}'

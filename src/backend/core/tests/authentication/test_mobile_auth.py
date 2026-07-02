"""Tests for the mobile (Capacitor) OIDC session handoff."""

import time
from importlib import import_module
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.test import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from core import factories
from core.api.viewsets.mobile_auth import _s256
from core.authentication.views import (
    MOBILE_AUTH_SESSION_KEY,
    MOBILE_AUTH_TOKEN_CACHE_PREFIX,
    OIDCAuthenticationCallbackView,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Reset the shared locmem cache before each test.

    The exchange endpoint throttles per IP and every test client posts from
    127.0.0.1, so a shared counter would leak requests across tests and make
    outcomes order-dependent.
    """
    cache.clear()


AUTHENTICATE_SETTINGS = {
    "MOBILE_AUTH_CALLBACK_SCHEMES": ["stmessagesa", "stmessagesb"],
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://oidc.test/authorize",
}


def _make_authenticated_session(user):
    """Create a server-side session authenticated as the given user."""
    engine = import_module(settings.SESSION_ENGINE)
    session = engine.SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "core.authentication.backends.OIDCAuthenticationBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return session


class TestMobileAuthenticationRequest:
    """Tests for the mobile parameters of the authenticate view."""

    @override_settings(**AUTHENTICATE_SETTINGS)
    def test_unknown_scheme_is_rejected(self):
        """A scheme not in the allowlist must be rejected as suspicious."""
        response = APIClient().get(
            reverse("oidc_authentication_init"),
            {"mobile_scheme": "evilapp", "code_challenge": "challenge"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(**AUTHENTICATE_SETTINGS)
    def test_missing_code_challenge_is_rejected(self):
        """A mobile login without PKCE challenge must be rejected."""
        response = APIClient().get(
            reverse("oidc_authentication_init"), {"mobile_scheme": "stmessagesa"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(**AUTHENTICATE_SETTINGS)
    def test_mobile_login_flags_the_session(self):
        """A valid mobile login redirects to the IdP and flags the session."""
        client = APIClient()
        response = client.get(
            reverse("oidc_authentication_init"),
            {"mobile_scheme": "stmessagesa", "code_challenge": "challenge"},
        )
        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"].startswith("https://oidc.test/authorize")
        mobile_auth = client.session[MOBILE_AUTH_SESSION_KEY]
        assert mobile_auth["scheme"] == "stmessagesa"
        assert mobile_auth["code_challenge"] == "challenge"

    @override_settings(**AUTHENTICATE_SETTINGS)
    def test_web_login_does_not_flag_the_session(self):
        """The web flow must not be affected by the mobile handoff support."""
        client = APIClient()
        response = client.get(reverse("oidc_authentication_init"))
        assert response.status_code == status.HTTP_302_FOUND
        assert MOBILE_AUTH_SESSION_KEY not in client.session


# `override_settings` only works as a class decorator on Django TestCase
# subclasses, so plain pytest classes apply it per method.
CALLBACK_SETTINGS = {
    "MOBILE_AUTH_CALLBACK_SCHEMES": ["stmessagesa", "stmessagesb"],
    "LOGIN_REDIRECT_URL": "/",
    "LOGIN_REDIRECT_URL_FAILURE": "/auth-failure",
}


class TestMobileAuthenticationCallback:
    """Tests for the mobile handoff performed by the callback view."""

    def _build_view(self, session_data=None):
        """Return a callback view bound to a request with a real session."""
        request = RequestFactory().get("/api/v1.0/callback/")
        SessionMiddleware(lambda _request: None).process_request(request)
        for key, value in (session_data or {}).items():
            request.session[key] = value
        request.session.save()

        user = factories.UserFactory()
        user.backend = "core.authentication.backends.OIDCAuthenticationBackend"

        view = OIDCAuthenticationCallbackView()
        view.request = request
        view.user = user
        return view

    @override_settings(**CALLBACK_SETTINGS)
    def test_mobile_login_success_redirects_to_the_app(self):
        """A mobile login ends with a deep link carrying a one-time token."""
        view = self._build_view(
            {
                MOBILE_AUTH_SESSION_KEY: {
                    "scheme": "stmessagesa",
                    "code_challenge": "challenge",
                    "created_at": time.time(),
                }
            }
        )
        response = view.login_success()

        assert response.status_code == status.HTTP_302_FOUND
        location = urlparse(response["Location"])
        assert location.scheme == "stmessagesa"
        assert location.netloc == "auth"

        token = parse_qs(location.query)["token"][0]
        payload = cache.get(f"{MOBILE_AUTH_TOKEN_CACHE_PREFIX}:{token}")
        assert payload["code_challenge"] == "challenge"
        # The session key is cycled by auth.login, the cache entry must hold
        # the post-login key so the exchanged cookie authenticates requests.
        assert payload["session_key"] == view.request.session.session_key
        assert MOBILE_AUTH_SESSION_KEY not in view.request.session

    @override_settings(**CALLBACK_SETTINGS)
    def test_web_login_success_is_unchanged(self):
        """Without the mobile flag, the callback keeps its web behavior."""
        view = self._build_view()
        response = view.login_success()
        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "/"

    @override_settings(**CALLBACK_SETTINGS)
    def test_stale_mobile_flag_is_ignored(self):
        """An abandoned mobile attempt must not hijack a later web login."""
        view = self._build_view(
            {
                MOBILE_AUTH_SESSION_KEY: {
                    "scheme": "stmessagesa",
                    "code_challenge": "challenge",
                    "created_at": time.time() - 3600,
                }
            }
        )
        response = view.login_success()
        assert response["Location"] == "/"
        assert MOBILE_AUTH_SESSION_KEY not in view.request.session

    @override_settings(**CALLBACK_SETTINGS)
    def test_mobile_login_failure_redirects_to_the_app(self):
        """A failed mobile login notifies the app through the deep link."""
        view = self._build_view(
            {
                MOBILE_AUTH_SESSION_KEY: {
                    "scheme": "stmessagesa",
                    "code_challenge": "challenge",
                    "created_at": time.time(),
                }
            }
        )
        response = view.login_failure()
        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "stmessagesa://auth?error=login_failed"

    @override_settings(**CALLBACK_SETTINGS)
    def test_web_login_failure_is_unchanged(self):
        """Without the mobile flag, a failed login keeps its web behavior."""
        view = self._build_view()
        response = view.login_failure()
        assert response["Location"] == "/auth-failure"


class TestMobileSessionExchange:
    """Tests for the one-time token → session cookie exchange endpoint."""

    VERIFIER = "mobile-app-code-verifier"
    TOKEN = "one-time-token"

    def _mint_token(self, user):
        """Create an authenticated session and the matching one-time token."""
        session = _make_authenticated_session(user)

        cache.set(
            f"{MOBILE_AUTH_TOKEN_CACHE_PREFIX}:{self.TOKEN}",
            {
                "session_key": session.session_key,
                "code_challenge": _s256(self.VERIFIER),
            },
            timeout=60,
        )
        return session

    def _exchange(self, client, **overrides):
        """POST the exchange payload, allowing per-test overrides."""
        payload = {"token": self.TOKEN, "code_verifier": self.VERIFIER, **overrides}
        return client.post(
            reverse("mobile-auth-exchange"),
            {key: value for key, value in payload.items() if value is not None},
            format="json",
        )

    def test_exchange_success_sets_the_session_cookie(self):
        """A valid exchange returns the session cookie and a CSRF token."""
        user = factories.UserFactory()
        session = self._mint_token(user)
        client = APIClient()

        response = self._exchange(client)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["csrf_token"]
        assert (
            response.cookies[settings.SESSION_COOKIE_NAME].value == session.session_key
        )
        # With CSRF_USE_SESSIONS the secret lives in the session: no csrftoken
        # cookie is ever emitted, the app only relies on the body token above.
        assert settings.CSRF_COOKIE_NAME not in response.cookies
        # The cookie carried over by the client now authenticates API calls.
        me_response = client.get("/api/v1.0/users/me/")
        assert me_response.status_code == status.HTTP_200_OK
        assert me_response.data["email"] == user.email

    def test_exchange_token_is_single_use(self):
        """Replaying a consumed token must be rejected."""
        self._mint_token(factories.UserFactory())
        client = APIClient()

        assert self._exchange(client).status_code == status.HTTP_200_OK
        assert self._exchange(client).status_code == status.HTTP_403_FORBIDDEN

    def test_exchange_lost_consume_race_is_rejected(self):
        """An exchange that loses the delete race must fail even with the payload.

        get() then delete() is not atomic: two concurrent exchanges can both
        read the payload, but the cache deletes the key exactly once and only
        the request whose delete() returns True may proceed.
        """
        self._mint_token(factories.UserFactory())
        client = APIClient()

        with patch("core.api.viewsets.mobile_auth.cache.delete", return_value=False):
            assert self._exchange(client).status_code == status.HTTP_403_FORBIDDEN

    def test_exchange_wrong_verifier_consumes_the_token(self):
        """A wrong PKCE verifier is rejected and burns the token."""
        self._mint_token(factories.UserFactory())
        client = APIClient()

        response = self._exchange(client, code_verifier="wrong-verifier")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        # The token was consumed by the failed attempt.
        assert self._exchange(client).status_code == status.HTTP_403_FORBIDDEN

    def test_exchange_missing_parameters(self):
        """Both the token and the verifier are required."""
        client = APIClient()
        assert (
            self._exchange(client, code_verifier=None).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        assert (
            self._exchange(client, token=None).status_code
            == status.HTTP_400_BAD_REQUEST
        )

    def test_exchange_non_string_parameters(self):
        """Non-string JSON values must be rejected instead of crashing _s256()."""
        self._mint_token(factories.UserFactory())
        client = APIClient()
        assert (
            self._exchange(client, code_verifier=1).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        assert (
            self._exchange(client, token=["a"]).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        # A body that is not a JSON object must not crash .get() either.
        list_body = APIClient().post(
            reverse("mobile-auth-exchange"), [1, 2], format="json"
        )
        assert list_body.status_code == status.HTTP_400_BAD_REQUEST

    def test_exchange_is_throttled_per_ip(self):
        """Repeated exchange attempts from one IP must be rate limited.

        The endpoint is anonymous and the one-time token is its only secret:
        without a cap, nothing slows down brute-force guessing.
        """
        client = APIClient()
        with patch.object(ScopedRateThrottle, "get_rate", return_value="2/minute"):
            for _ in range(2):
                assert self._exchange(client).status_code == status.HTTP_403_FORBIDDEN
            response = self._exchange(client)

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_exchange_expired_session(self):
        """A token referencing a vanished session must be rejected."""
        cache.set(
            f"{MOBILE_AUTH_TOKEN_CACHE_PREFIX}:{self.TOKEN}",
            {
                "session_key": "vanished-session-key",
                "code_challenge": _s256(self.VERIFIER),
            },
            timeout=60,
        )
        response = self._exchange(APIClient())
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestMobileLogout:
    """Tests for the mobile logout endpoint.

    Unlike `/logout/` it must end the Django session without the RP-initiated
    IdP logout, so the cross-app SSO session survives.
    """

    def _login(self, client, user):
        """Attach an authenticated session cookie to the client."""
        session = _make_authenticated_session(user)
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return session

    def test_logout_flushes_the_server_side_session(self):
        """Logout must invalidate the session itself, not only the cookie."""
        user = factories.UserFactory()
        client = APIClient()
        session = self._login(client, user)

        response = client.post(reverse("mobile-auth-logout"))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        engine = import_module(settings.SESSION_ENGINE)
        assert not engine.SessionStore().exists(session.session_key)
        # Replaying the old cookie (e.g. a cookie jar the app failed to clear)
        # must not authenticate anymore.
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        me_response = client.get("/api/v1.0/users/me/")
        assert me_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_is_a_noop_for_anonymous_requests(self):
        """A logout without a live session must succeed silently."""
        response = APIClient().post(reverse("mobile-auth-logout"))
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_logout_enforces_csrf(self):
        """The session-authenticated POST must echo the CSRF token."""
        client = APIClient(enforce_csrf_checks=True)
        self._login(client, factories.UserFactory())

        response = client.post(reverse("mobile-auth-logout"))

        assert response.status_code == status.HTTP_403_FORBIDDEN

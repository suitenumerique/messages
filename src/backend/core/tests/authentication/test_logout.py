"""Tests for the logout flow, IdP-terminating even for anonymous sessions."""

import time
from urllib.parse import parse_qs, urlparse

from django.test.utils import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories
from core.authentication.views import MOBILE_LOGOUT_SESSION_KEY

pytestmark = pytest.mark.django_db

LOGOUT_SETTINGS = {
    "OIDC_OP_LOGOUT_ENDPOINT": "https://oidc.test/logout",
    "LOGOUT_REDIRECT_URL": "https://app.test/",
}

MOBILE_LOGOUT_SETTINGS = {
    **LOGOUT_SETTINGS,
    "MOBILE_AUTH_CALLBACK_SCHEMES": ["stmessagesa"],
}


class TestLogoutView:
    """Tests for the logout view."""

    @override_settings(**LOGOUT_SETTINGS)
    def test_authenticated_user_goes_through_idp_logout(self):
        """An authenticated logout must initiate the IdP round-trip, keeping
        the session alive until the callback validates the state."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_login(user)
        session = client.session
        session["oidc_id_token"] = "fake-id-token"
        session.save()

        response = client.get(reverse("oidc_logout_custom"))

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"].startswith("https://oidc.test/logout?")
        query = parse_qs(urlparse(response["Location"]).query)
        assert query["id_token_hint"] == ["fake-id-token"]
        assert query["post_logout_redirect_uri"][0].endswith(
            reverse("oidc_logout_callback")
        )

        session = client.session
        assert query["state"][0] in session["oidc_states"]
        assert "_auth_user_id" in session

    @override_settings(**LOGOUT_SETTINGS)
    def test_anonymous_session_with_id_token_goes_through_idp_logout(self):
        """A failed sign-in leaves an anonymous session holding the id_token:
        logging out must still terminate the IdP session, which would
        otherwise keep signing the same identity back in."""
        client = APIClient()
        session = client.session
        session["oidc_id_token"] = "fake-id-token"
        session.save()

        response = client.get(reverse("oidc_logout_custom"))

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"].startswith("https://oidc.test/logout?")
        query = parse_qs(urlparse(response["Location"]).query)
        assert query["id_token_hint"] == ["fake-id-token"]
        assert query["state"][0] in client.session["oidc_states"]

    @override_settings(**LOGOUT_SETTINGS)
    def test_without_id_token_logs_out_locally(self):
        """With no IdP session to terminate, the logout stays local."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_login(user)

        response = client.get(reverse("oidc_logout_custom"))

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "https://app.test/"
        assert "_auth_user_id" not in client.session


class TestLogoutCallbackView:
    """Tests for the logout callback (lasuite's view, exposed by our urlconf)."""

    @override_settings(**LOGOUT_SETTINGS)
    def test_authenticated_callback_logs_out_and_lands_on_homepage(self):
        """The lasuite callback completes the round-trip: state validated,
        session ended, user back on the homepage."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_login(user)
        session = client.session
        session["oidc_states"] = {"logout-state": {}}
        session.save()

        response = client.get(
            reverse("oidc_logout_callback"), {"state": "logout-state"}
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "https://app.test/"
        assert "_auth_user_id" not in client.session


class TestMobileLogoutHandoff:
    """Tests for the mobile handoff of the IdP-terminating logout.

    A logout initiated by a mobile app runs in the system browser and must end
    on a deep link so the sheet closes and the app can clear its local state.
    """

    def _login_with_id_token(self, client):
        """Authenticate the client with an id_token stored in the session."""
        client.force_login(factories.UserFactory())
        session = client.session
        session["oidc_id_token"] = "fake-id-token"
        session.save()

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_mobile_logout_goes_through_idp_and_flags_the_session(self):
        """A mobile logout must run the IdP round-trip and flag the session."""
        client = APIClient()
        self._login_with_id_token(client)

        response = client.get(
            reverse("oidc_logout_custom"), {"mobile_scheme": "stmessagesa"}
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"].startswith("https://oidc.test/logout?")
        query = parse_qs(urlparse(response["Location"]).query)
        mobile_logout = client.session[MOBILE_LOGOUT_SESSION_KEY]
        assert mobile_logout["scheme"] == "stmessagesa"
        assert mobile_logout["state"] == query["state"][0]

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_mobile_logout_unknown_scheme_is_rejected(self):
        """A scheme not in the allowlist must be rejected as suspicious."""
        response = APIClient().get(
            reverse("oidc_logout_custom"), {"mobile_scheme": "evilapp"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_mobile_logout_without_id_token_deep_links_immediately(self):
        """With no IdP round-trip possible, the deep link must close the flow."""
        client = APIClient()
        client.force_login(factories.UserFactory())

        response = client.get(
            reverse("oidc_logout_custom"), {"mobile_scheme": "stmessagesa"}
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "stmessagesa://logout"
        assert "_auth_user_id" not in client.session

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_mobile_callback_deep_links_to_the_app(self):
        """The callback must end the session then hand control to the app."""
        client = APIClient()
        client.force_login(factories.UserFactory())
        session = client.session
        session["oidc_states"] = {"logout-state": {}}
        session[MOBILE_LOGOUT_SESSION_KEY] = {
            "scheme": "stmessagesa",
            "state": "logout-state",
            "created_at": time.time(),
        }
        session.save()

        response = client.get(
            reverse("oidc_logout_callback"), {"state": "logout-state"}
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "stmessagesa://logout"
        assert "_auth_user_id" not in client.session

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_stateless_preflight_does_not_consume_the_mobile_flag(self):
        """Some IdPs send a state-less preflight before the actual callback:
        it must leave the flag and the session untouched so the real
        callback still ends on the deep link."""
        client = APIClient()
        client.force_login(factories.UserFactory())
        session = client.session
        session["oidc_states"] = {"logout-state": {}}
        session[MOBILE_LOGOUT_SESSION_KEY] = {
            "scheme": "stmessagesa",
            "state": "logout-state",
            "created_at": time.time(),
        }
        session.save()

        response = client.get(reverse("oidc_logout_callback"))

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "https://app.test/"
        assert client.session[MOBILE_LOGOUT_SESSION_KEY]["scheme"] == "stmessagesa"
        assert "_auth_user_id" in client.session

        response = client.get(
            reverse("oidc_logout_callback"), {"state": "logout-state"}
        )

        assert response["Location"] == "stmessagesa://logout"
        assert "_auth_user_id" not in client.session

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_unknown_state_does_not_consume_the_mobile_flag(self):
        """A callback with a state unknown to the session is rejected by the
        parent view: the flag must survive for the legitimate callback."""
        client = APIClient()
        client.force_login(factories.UserFactory())
        session = client.session
        session["oidc_states"] = {"logout-state": {}}
        session[MOBILE_LOGOUT_SESSION_KEY] = {
            "scheme": "stmessagesa",
            "state": "logout-state",
            "created_at": time.time(),
        }
        session.save()

        response = client.get(
            reverse("oidc_logout_callback"), {"state": "forged-state"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert client.session[MOBILE_LOGOUT_SESSION_KEY]["scheme"] == "stmessagesa"

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_another_valid_state_keeps_the_web_redirect(self):
        """A second flow started from the same browser session leaves another
        valid state behind: a still-fresh mobile flag must not turn that
        callback into a deep-link redirect on a web page."""
        client = APIClient()
        client.force_login(factories.UserFactory())
        session = client.session
        session["oidc_states"] = {"web-state": {}, "mobile-state": {}}
        session[MOBILE_LOGOUT_SESSION_KEY] = {
            "scheme": "stmessagesa",
            "state": "mobile-state",
            "created_at": time.time(),
        }
        session.save()

        response = client.get(reverse("oidc_logout_callback"), {"state": "web-state"})

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "https://app.test/"
        assert "_auth_user_id" not in client.session

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_anonymous_mobile_callback_still_deep_links(self):
        """A failed sign-in leaves the session anonymous while the IdP logout
        round-trip runs: the callback must still close the sheet via the
        deep link even though the parent view returns early."""
        client = APIClient()
        session = client.session
        session["oidc_states"] = {"logout-state": {}}
        session[MOBILE_LOGOUT_SESSION_KEY] = {
            "scheme": "stmessagesa",
            "state": "logout-state",
            "created_at": time.time(),
        }
        session.save()

        response = client.get(
            reverse("oidc_logout_callback"), {"state": "logout-state"}
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "stmessagesa://logout"

    @override_settings(**MOBILE_LOGOUT_SETTINGS)
    def test_stale_mobile_flag_keeps_the_web_redirect(self):
        """An abandoned mobile logout must not hijack a later web logout."""
        client = APIClient()
        client.force_login(factories.UserFactory())
        session = client.session
        session["oidc_states"] = {"logout-state": {}}
        session[MOBILE_LOGOUT_SESSION_KEY] = {
            "scheme": "stmessagesa",
            "state": "logout-state",
            "created_at": time.time() - 3600,
        }
        session.save()

        response = client.get(
            reverse("oidc_logout_callback"), {"state": "logout-state"}
        )

        assert response.status_code == status.HTTP_302_FOUND
        assert response["Location"] == "https://app.test/"
        assert "_auth_user_id" not in client.session

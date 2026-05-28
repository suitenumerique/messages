"""Tests for the logout flow, IdP-terminating even for anonymous sessions."""

from urllib.parse import parse_qs, urlparse

from django.test.utils import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories

pytestmark = pytest.mark.django_db

LOGOUT_SETTINGS = {
    "OIDC_OP_LOGOUT_ENDPOINT": "https://oidc.test/logout",
    "LOGOUT_REDIRECT_URL": "https://app.test/",
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

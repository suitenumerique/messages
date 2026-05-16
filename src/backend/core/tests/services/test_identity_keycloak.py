"""Tests for the Keycloak identity service helpers."""
# pylint: disable=unused-argument

import json
from unittest.mock import MagicMock, patch

from django.test import override_settings

import pytest
from keycloak.exceptions import KeycloakError

from core.services.identity import keycloak as keycloak_service


@pytest.fixture(name="keycloak_admin_mock")
def fixture_keycloak_admin_mock():
    """Patch get_keycloak_admin_client and yield the returned mock client."""
    with patch.object(keycloak_service, "get_keycloak_admin_client") as factory:
        client = MagicMock()
        factory.return_value = client
        yield client


def test_reset_password_re_enables_disabled_user(keycloak_admin_mock):
    """A disabled Keycloak user is re-enabled before the password is reset."""
    keycloak_admin_mock.get_users.return_value = [
        {"id": "kc-user-id", "enabled": False}
    ]

    new_password = keycloak_service.reset_keycloak_user_password(
        "user@example.local", new_password="NewPass123!"
    )

    assert new_password == "NewPass123!"
    keycloak_admin_mock.update_user.assert_called_once_with(
        user_id="kc-user-id", payload={"enabled": True}
    )
    keycloak_admin_mock.clear_bruteforce_attempts_for_user.assert_called_once_with(
        user_id="kc-user-id"
    )
    keycloak_admin_mock.set_user_password.assert_called_once_with(
        user_id="kc-user-id", password="NewPass123!", temporary=True
    )


def test_reset_password_skips_re_enable_when_already_enabled(keycloak_admin_mock):
    """An already-enabled user is not re-enabled, but brute-force is still cleared."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id", "enabled": True}]

    keycloak_service.reset_keycloak_user_password(
        "user@example.local", new_password="NewPass123!"
    )

    keycloak_admin_mock.update_user.assert_not_called()
    keycloak_admin_mock.clear_bruteforce_attempts_for_user.assert_called_once()
    keycloak_admin_mock.set_user_password.assert_called_once()


def test_reset_password_swallows_brute_force_error(keycloak_admin_mock):
    """Failure to clear brute-force counters must not abort the password reset."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id", "enabled": True}]
    keycloak_admin_mock.clear_bruteforce_attempts_for_user.side_effect = KeycloakError(
        "boom"
    )

    keycloak_service.reset_keycloak_user_password(
        "user@example.local", new_password="NewPass123!"
    )

    keycloak_admin_mock.set_user_password.assert_called_once()


def test_reset_password_raises_when_user_not_found(keycloak_admin_mock):
    """A missing user surfaces a clear ValueError; nothing is mutated."""
    keycloak_admin_mock.get_users.return_value = []

    with pytest.raises(ValueError, match="not found"):
        keycloak_service.reset_keycloak_user_password("ghost@example.local")

    keycloak_admin_mock.set_user_password.assert_not_called()


def test_set_realm_role_assigns_when_missing(keycloak_admin_mock):
    """The role is assigned only when the user does not already have it."""
    role = {"id": "role-id", "name": "mandatory-totp"}
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_role_by_id.return_value = role
    keycloak_admin_mock.get_realm_roles_of_user.return_value = []

    keycloak_service.set_realm_role("user@example.local", "role-id", assigned=True)

    keycloak_admin_mock.assign_realm_roles.assert_called_once_with(
        user_id="kc-user-id", roles=[role]
    )
    keycloak_admin_mock.delete_realm_roles_of_user.assert_not_called()


def test_set_realm_role_is_idempotent_when_already_assigned(keycloak_admin_mock):
    """Re-assigning a role the user already carries is a no-op."""
    role = {"id": "role-id", "name": "mandatory-totp"}
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_role_by_id.return_value = role
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [role]

    keycloak_service.set_realm_role("user@example.local", "role-id", assigned=True)

    keycloak_admin_mock.assign_realm_roles.assert_not_called()


def test_set_realm_role_removes_when_present(keycloak_admin_mock):
    """Removing a role that the user carries triggers a single delete call."""
    role = {"id": "role-id", "name": "mandatory-totp"}
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_role_by_id.return_value = role
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [role]

    keycloak_service.set_realm_role("user@example.local", "role-id", assigned=False)

    keycloak_admin_mock.delete_realm_roles_of_user.assert_called_once_with(
        user_id="kc-user-id", roles=[role]
    )


def test_is_mandatory_totp_enabled():
    """All three settings must be present and the IDP must be Keycloak."""
    with override_settings(
        FEATURE_MAILDOMAIN_MANAGE_TOTP=True,
        KEYCLOAK_TOTP_ROLE_ID="role-id",
        IDENTITY_PROVIDER="keycloak",
    ):
        assert keycloak_service.is_mandatory_totp_enabled() is True

    with override_settings(
        FEATURE_MAILDOMAIN_MANAGE_TOTP=False,
        KEYCLOAK_TOTP_ROLE_ID="role-id",
        IDENTITY_PROVIDER="keycloak",
    ):
        assert keycloak_service.is_mandatory_totp_enabled() is False

    with override_settings(
        FEATURE_MAILDOMAIN_MANAGE_TOTP=True,
        KEYCLOAK_TOTP_ROLE_ID=None,
        IDENTITY_PROVIDER="keycloak",
    ):
        assert keycloak_service.is_mandatory_totp_enabled() is False

    with override_settings(
        FEATURE_MAILDOMAIN_MANAGE_TOTP=True,
        KEYCLOAK_TOTP_ROLE_ID="role-id",
        IDENTITY_PROVIDER="oidc",
    ):
        assert keycloak_service.is_mandatory_totp_enabled() is False


def _stub_bulk_response(keycloak_admin_mock, *, members):
    """Wire keycloak_admin.connection.raw_post to return a 200 with these members."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"members": list(members)}
    keycloak_admin_mock.connection.raw_post.return_value = response
    return response


def test_batch_realm_role_membership_calls_custom_endpoint(keycloak_admin_mock):
    """One POST to the custom endpoint resolves the whole page."""
    _stub_bulk_response(keycloak_admin_mock, members=["alice@example.local"])

    result = keycloak_service.batch_realm_role_membership(
        ["alice@example.local", "bob@example.local"], "role-id"
    )

    assert result == {"alice@example.local": True, "bob@example.local": False}
    call = keycloak_admin_mock.connection.raw_post.call_args
    assert call.args[0].endswith("/bulk-role-membership/check")
    body = json.loads(call.kwargs["data"])
    assert body == {
        "role_id": "role-id",
        "usernames": ["alice@example.local", "bob@example.local"],
    }


def test_batch_realm_role_membership_empty_input_skips_endpoint(keycloak_admin_mock):
    """An empty input short-circuits before any HTTP call is issued."""
    assert keycloak_service.batch_realm_role_membership([], "role-id") == {}
    keycloak_admin_mock.connection.raw_post.assert_not_called()


def test_batch_realm_role_membership_is_case_insensitive(keycloak_admin_mock):
    """Caller casing is preserved as keys; membership decision is lowercased."""
    _stub_bulk_response(keycloak_admin_mock, members=["alice@example.local"])

    result = keycloak_service.batch_realm_role_membership(
        ["Alice@Example.local", "Carol@Example.local"], "role-id"
    )

    assert result == {
        "Alice@Example.local": True,
        "Carol@Example.local": False,
    }


def test_batch_realm_role_membership_raises_on_http_error(keycloak_admin_mock):
    """A non-2xx response surfaces as an exception — the caller wraps it."""
    response = MagicMock()
    response.raise_for_status.side_effect = RuntimeError("HTTP 500")
    keycloak_admin_mock.connection.raw_post.return_value = response

    with pytest.raises(RuntimeError):
        keycloak_service.batch_realm_role_membership(["alice@example.local"], "role-id")


def test_has_realm_role(keycloak_admin_mock):
    """has_realm_role returns True when the role id is in the user's role list."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [
        {"id": "other-role"},
        {"id": "role-id"},
    ]
    assert keycloak_service.has_realm_role("user@example.local", "role-id") is True
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [{"id": "other-role"}]
    assert keycloak_service.has_realm_role("user@example.local", "role-id") is False


def test_get_keycloak_user_id_uses_exact_match(keycloak_admin_mock):
    """The lookup must pass ``exact=True`` so substring matches don't slip in."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_service._get_keycloak_user_id("user@example.local")  # pylint: disable=protected-access
    keycloak_admin_mock.get_users.assert_called_once_with(
        {"username": "user@example.local", "exact": True}
    )


def test_get_keycloak_user_id_raises_on_ambiguous(keycloak_admin_mock):
    """Two matches means upstream search returned junk — raise, don't guess."""
    keycloak_admin_mock.get_users.return_value = [
        {"id": "id-a"},
        {"id": "id-b"},
    ]
    with pytest.raises(ValueError, match="Ambiguous"):
        keycloak_service._get_keycloak_user_id("user@example.local")  # pylint: disable=protected-access


def test_reset_totp_deletes_otp_credentials_and_adds_required_action(
    keycloak_admin_mock,
):
    """OTP credentials are removed and CONFIGURE_TOTP becomes a required action."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_credentials.return_value = [
        {"id": "cred-1", "type": "password"},
        {"id": "cred-2", "type": "otp"},
        {"id": "cred-3", "type": "OTP"},
    ]
    keycloak_admin_mock.get_user.return_value = {"requiredActions": []}

    result = keycloak_service.reset_keycloak_user_totp("user@example.local")

    assert result == {"removed_credentials": 2}
    delete_calls = keycloak_admin_mock.delete_credential.call_args_list
    deleted_ids = sorted(c.kwargs["credential_id"] for c in delete_calls)
    assert deleted_ids == ["cred-2", "cred-3"]
    keycloak_admin_mock.update_user.assert_called_once_with(
        user_id="kc-user-id",
        payload={"requiredActions": ["CONFIGURE_TOTP"]},
    )


def test_reset_totp_skips_required_action_update_when_already_present(
    keycloak_admin_mock,
):
    """If CONFIGURE_TOTP is already required, update_user is not called again."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_credentials.return_value = []
    keycloak_admin_mock.get_user.return_value = {"requiredActions": ["CONFIGURE_TOTP"]}

    keycloak_service.reset_keycloak_user_totp("user@example.local")

    keycloak_admin_mock.update_user.assert_not_called()

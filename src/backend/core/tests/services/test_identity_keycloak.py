"""Tests for the Keycloak identity service helpers."""
# pylint: disable=protected-access, unused-argument

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


def test_set_realm_role_updates_cache_on_change(keycloak_admin_mock):
    """Each successful Keycloak write also refreshes the Redis membership cache."""
    role = {"id": "role-id", "name": "mandatory-totp"}
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_role_by_id.return_value = role

    # Assign branch: cache call mirrors the Keycloak write.
    keycloak_admin_mock.get_realm_roles_of_user.return_value = []
    with patch.object(keycloak_service, "update_cached_realm_role_member") as upd:
        keycloak_service.set_realm_role("user@example.local", "role-id", assigned=True)
        upd.assert_called_once_with("role-id", "user@example.local", present=True)

    # Remove branch: same invariant.
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [role]
    with patch.object(keycloak_service, "update_cached_realm_role_member") as upd:
        keycloak_service.set_realm_role("user@example.local", "role-id", assigned=False)
        upd.assert_called_once_with("role-id", "user@example.local", present=False)


def test_set_realm_role_skips_cache_when_idempotent(keycloak_admin_mock):
    """No Keycloak write, no cache write — the helper is a strict no-op."""
    role = {"id": "role-id", "name": "mandatory-totp"}
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_role_by_id.return_value = role
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [role]

    with patch.object(keycloak_service, "update_cached_realm_role_member") as upd:
        keycloak_service.set_realm_role("user@example.local", "role-id", assigned=True)
        upd.assert_not_called()


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


def test_fetch_realm_role_members(keycloak_admin_mock):
    """A single call returns the set of usernames assigned to the role."""
    keycloak_admin_mock.get_realm_role_by_id.return_value = {
        "id": "role-id",
        "name": "mandatory-totp",
    }
    keycloak_admin_mock.get_realm_role_members.return_value = [
        {"username": "alice@example.local"},
        {"username": "bob@example.local"},
        {},  # malformed entry: ignored
    ]

    result = keycloak_service._fetch_realm_role_members("role-id")

    assert result == {"alice@example.local", "bob@example.local"}
    keycloak_admin_mock.get_realm_role_members.assert_called_once_with(
        role_name="mandatory-totp"
    )


def test_fetch_realm_role_members_returns_empty_when_role_missing(
    keycloak_admin_mock,
):
    """When the role id is unknown, the helper returns an empty set and skips lookups."""
    keycloak_admin_mock.get_realm_role_by_id.return_value = None
    assert keycloak_service._fetch_realm_role_members("nope") == set()
    keycloak_admin_mock.get_realm_role_members.assert_not_called()


@pytest.fixture(name="redis_mock")
def fixture_redis_mock():
    """In-memory fake of the redis client subset we use (EXISTS/SMEMBERS/SADD/SREM)."""

    class FakeRedis:
        """Minimal in-memory stand-in for the redis client used in tests."""

        def __init__(self):
            self.sets = {}

        def exists(self, key):
            """Return 1 if the key has any stored data, else 0."""
            return 1 if key in self.sets else 0

        def smembers(self, key):
            """Return a copy of the members stored under the key."""
            return set(self.sets.get(key, set()))

        def sismember(self, key, member):
            """Return 1 if ``member`` is in the set under ``key``, else 0."""
            stored = self.sets.get(key, set())
            wire = member if isinstance(member, bytes) else member.encode()
            return 1 if wire in stored else 0

        def sadd(self, key, *members):
            """Add ``members`` to the set under ``key``."""
            stored = self.sets.setdefault(key, set())
            for m in members:
                stored.add(m if isinstance(m, bytes) else m.encode())

        def srem(self, key, *members):
            """Remove ``members`` from the set under ``key`` if present."""
            stored = self.sets.get(key)
            if stored is None:
                return
            for m in members:
                stored.discard(m if isinstance(m, bytes) else m.encode())

        def delete(self, key):
            """Drop the key (and its stored set) from the fake store."""
            self.sets.pop(key, None)

        def expire(self, key, ttl):
            """No-op: the fake does not implement TTL semantics."""
            return None

        def pipeline(self):
            """Return a fresh fake pipeline bound to this client."""
            return _FakePipeline(self)

    class _FakePipeline:
        """Records redis ops then replays them against the FakeRedis client."""

        def __init__(self, client):
            self.client = client
            self.ops = []

        def delete(self, key):
            """Queue a DELETE op."""
            self.ops.append(("delete", key))
            return self

        def sadd(self, key, *members):
            """Queue an SADD op."""
            self.ops.append(("sadd", key, members))
            return self

        def sismember(self, key, member):
            """Queue an SISMEMBER op."""
            self.ops.append(("sismember", key, member))
            return self

        def expire(self, key, ttl):
            """Queue an EXPIRE op."""
            self.ops.append(("expire", key, ttl))
            return self

        def execute(self):
            """Replay queued ops against the FakeRedis client and return results."""
            results = []
            for op in self.ops:
                if op[0] == "delete":
                    self.client.delete(op[1])
                    results.append(None)
                elif op[0] == "sadd":
                    self.client.sadd(op[1], *op[2])
                    results.append(None)
                elif op[0] == "sismember":
                    results.append(self.client.sismember(op[1], op[2]))
                elif op[0] == "expire":
                    self.client.expire(op[1], op[2])
                    results.append(None)
            return results

    fake = FakeRedis()
    with patch.object(keycloak_service, "_redis_client", return_value=fake):
        yield fake


def test_batch_realm_role_membership_populates_on_miss(redis_mock, keycloak_admin_mock):
    """First call populates the SET from Keycloak then pipelines SISMEMBERs."""
    keycloak_admin_mock.get_realm_role_by_id.return_value = {
        "id": "role-id",
        "name": "mandatory-totp",
    }
    keycloak_admin_mock.get_realm_role_members.return_value = [
        {"username": "alice@example.local"},
    ]

    result = keycloak_service.batch_realm_role_membership(
        ["alice@example.local", "bob@example.local"], "role-id"
    )

    assert result == {"alice@example.local": True, "bob@example.local": False}
    # Cache populated; sentinel keeps the key observable even when empty.
    key = keycloak_service._realm_role_members_cache_key("role-id")
    assert redis_mock.sets[key] == {
        keycloak_service._CACHE_SENTINEL,
        b"alice@example.local",
    }
    # Repeat call shouldn't refetch Keycloak.
    keycloak_service.batch_realm_role_membership(["alice@example.local"], "role-id")
    assert keycloak_admin_mock.get_realm_role_members.call_count == 1


def test_batch_realm_role_membership_pipelines_sismember(
    redis_mock, keycloak_admin_mock
):
    """One pipelined round-trip yields a {username: bool} for the page."""
    keycloak_admin_mock.get_realm_role_by_id.return_value = {
        "id": "role-id",
        "name": "mandatory-totp",
    }
    keycloak_admin_mock.get_realm_role_members.return_value = [
        {"username": "alice@example.local"},
        {"username": "carol@example.local"},
    ]

    result = keycloak_service.batch_realm_role_membership(
        ["alice@example.local", "bob@example.local", "carol@example.local"],
        "role-id",
    )

    assert result == {
        "alice@example.local": True,
        "bob@example.local": False,
        "carol@example.local": True,
    }


def test_batch_realm_role_membership_empty_input_skips_redis(redis_mock):
    """An empty input list short-circuits before any Redis call is issued."""
    assert keycloak_service.batch_realm_role_membership([], "role-id") == {}
    assert redis_mock.sets == {}


def test_batch_realm_role_membership_without_redis_uses_has_realm_role(
    keycloak_admin_mock,
):
    """Without Redis, the helper falls back to per-user Keycloak lookups."""
    keycloak_admin_mock.get_users.return_value = [{"id": "kc-user-id"}]
    keycloak_admin_mock.get_realm_roles_of_user.return_value = [{"id": "role-id"}]

    with patch.object(keycloak_service, "_redis_client", return_value=None):
        result = keycloak_service.batch_realm_role_membership(
            ["alice@example.local"], "role-id"
        )

    assert result == {"alice@example.local": True}


def test_update_cached_realm_role_member_adds_to_existing_set(redis_mock):
    """A present=True update adds the username to the cached set."""
    key = keycloak_service._realm_role_members_cache_key("role-id")
    redis_mock.sets[key] = {keycloak_service._CACHE_SENTINEL, b"alice@example.local"}

    keycloak_service.update_cached_realm_role_member(
        "role-id", "bob@example.local", present=True
    )

    assert redis_mock.sets[key] == {
        keycloak_service._CACHE_SENTINEL,
        b"alice@example.local",
        b"bob@example.local",
    }


def test_update_cached_realm_role_member_removes_from_existing_set(redis_mock):
    """A present=False update removes the username from the cached set."""
    key = keycloak_service._realm_role_members_cache_key("role-id")
    redis_mock.sets[key] = {
        keycloak_service._CACHE_SENTINEL,
        b"alice@example.local",
        b"bob@example.local",
    }

    keycloak_service.update_cached_realm_role_member(
        "role-id", "alice@example.local", present=False
    )

    assert redis_mock.sets[key] == {
        keycloak_service._CACHE_SENTINEL,
        b"bob@example.local",
    }


def test_update_cached_realm_role_member_noop_when_cache_empty(redis_mock):
    """Empty cache → next read will repopulate; nothing to update now."""
    keycloak_service.update_cached_realm_role_member(
        "role-id", "alice@example.local", present=True
    )
    assert redis_mock.sets == {}


def test_update_cached_realm_role_member_noop_without_redis():
    """Without Redis the helper is a no-op."""
    with patch.object(keycloak_service, "_redis_client", return_value=None):
        # Just shouldn't raise.
        keycloak_service.update_cached_realm_role_member(
            "role-id", "alice@example.local", present=True
        )


def test_update_cached_realm_role_member_lowercases_username(redis_mock):
    """Redis SET ops are case-sensitive; Keycloak isn't — normalize on write."""
    key = keycloak_service._realm_role_members_cache_key("role-id")
    redis_mock.sets[key] = {keycloak_service._CACHE_SENTINEL}

    keycloak_service.update_cached_realm_role_member(
        "role-id", "Alice@Example.local", present=True
    )

    assert redis_mock.sets[key] == {
        keycloak_service._CACHE_SENTINEL,
        b"alice@example.local",
    }


def test_update_cached_realm_role_member_swallows_redis_errors():
    """Cache hiccups must not fail the request: source-of-truth already wrote."""
    broken = MagicMock()
    broken.exists.side_effect = RuntimeError("redis down")

    with patch.object(keycloak_service, "_redis_client", return_value=broken):
        # Doesn't raise.
        keycloak_service.update_cached_realm_role_member(
            "role-id", "alice@example.local", present=True
        )


def test_mixed_case_username_roundtrips_through_cache(redis_mock, keycloak_admin_mock):
    """End-to-end: write a mixed-case username, read with any casing.

    Reproduces the regression where ``SADD`` and ``SISMEMBER`` use byte-exact
    comparison: write ``Alice@Foo.test`` then read ``alice@foo.test`` and we
    should still get ``True``. Keycloak treats usernames case-insensitively;
    our cache must too.
    """
    keycloak_admin_mock.get_realm_role_by_id.return_value = {
        "id": "role-id",
        "name": "mandatory-totp",
    }
    # Cache populates empty (the role exists but has no members yet).
    keycloak_admin_mock.get_realm_role_members.return_value = []

    # Admin toggles TOTP on for the mixed-case username.
    keycloak_service.batch_realm_role_membership(["seed@anything"], "role-id")
    keycloak_service.update_cached_realm_role_member(
        "role-id", "Alice@Foo.test", present=True
    )

    # All-lowercase lookup hits.
    assert keycloak_service.batch_realm_role_membership(
        ["alice@foo.test"], "role-id"
    ) == {"alice@foo.test": True}

    # Mixed-case lookup hits too.
    assert keycloak_service.batch_realm_role_membership(
        ["AliCe@FOO.test"], "role-id"
    ) == {"AliCe@FOO.test": True}

    # Removing via mixed case still cleans the lowercased entry.
    keycloak_service.update_cached_realm_role_member(
        "role-id", "ALICE@foo.TEST", present=False
    )
    assert keycloak_service.batch_realm_role_membership(
        ["alice@foo.test"], "role-id"
    ) == {"alice@foo.test": False}


def test_batch_realm_role_membership_lowercases_inputs(redis_mock, keycloak_admin_mock):
    """Membership check normalizes case before SISMEMBER."""
    key = keycloak_service._realm_role_members_cache_key("role-id")
    redis_mock.sets[key] = {keycloak_service._CACHE_SENTINEL, b"alice@example.local"}

    result = keycloak_service.batch_realm_role_membership(
        ["Alice@Example.local", "Carol@Example.local"], "role-id"
    )

    # Result keys preserve caller casing, but the membership decision uses
    # the lowercased form.
    assert result == {
        "Alice@Example.local": True,
        "Carol@Example.local": False,
    }


def test_fetch_realm_role_members_lowercases_keycloak_usernames(
    keycloak_admin_mock,
):
    """Usernames pulled from Keycloak are lowercased before being cached."""
    keycloak_admin_mock.get_realm_role_by_id.return_value = {
        "id": "role-id",
        "name": "mandatory-totp",
    }
    keycloak_admin_mock.get_realm_role_members.return_value = [
        {"username": "Alice@Example.local"},
    ]

    assert keycloak_service._fetch_realm_role_members("role-id") == {
        "alice@example.local"
    }


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

#!/usr/bin/env python3
"""End-to-end test for the bulk-role-membership Keycloak provider.

Runs against a Keycloak instance already brought up by `docker compose
up keycloak`. Exercises happy paths, input validation, role-scope
guards, authentication, authorization, SQL-injection safety, and HTTP
semantics. Creates and deletes its own roles and users so the realm
state is unchanged when the script finishes.

Usage (preferred): `make test-keycloak` from the repo root.
Direct: `python test_bulk_role_membership.py` against
`KEYCLOAK_URL` (default `http://localhost:8902`).
"""

import json
import os
import sys
import urllib3
import uuid

import requests
from keycloak import KeycloakAdmin, KeycloakOpenID

# We use verify=False for the dev keycloak which is plain http; silence
# urllib3's per-call warning so the test output stays readable.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8902")
TARGET_REALM = os.environ.get("KEYCLOAK_REALM", "messages")
CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "rest-api")
CLIENT_SECRET = os.environ.get(
    "KEYCLOAK_CLIENT_SECRET", "ServiceAccountClientSecretForDev"
)
MASTER_ADMIN_USER = os.environ.get("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
MASTER_ADMIN_PASS = os.environ.get("KC_BOOTSTRAP_ADMIN_PASSWORD", "admin")

ENDPOINT_PATH = f"/realms/{TARGET_REALM}/bulk-role-membership/check"
ENDPOINT_URL = f"{KEYCLOAK_URL}{ENDPOINT_PATH}"


def _post_with_token(token, body, *, raw_data=None, content_type="application/json"):
    """POST to the endpoint with an explicit bearer token (or None)."""
    headers = {"Content-Type": content_type}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return requests.post(
        ENDPOINT_URL,
        data=raw_data if raw_data is not None else json.dumps(body),
        headers=headers,
        verify=False,
        timeout=10,
    )


def _password_token(realm, client_id, username, password, *, client_secret=None):
    """Fetch an access token via the OIDC password grant."""
    data = {
        "grant_type": "password",
        "client_id": client_id,
        "username": username,
        "password": password,
    }
    if client_secret is not None:
        data["client_secret"] = client_secret
    response = requests.post(
        f"{KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/token",
        data=data,
        verify=False,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _ok(label):
    print(f"OK: {label}")


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"test-bulk-role-{suffix}"
    empty_role_name = f"test-bulk-empty-{suffix}"
    lowpriv_username = f"test-bulk-lowpriv-{suffix}@example.test"
    lowpriv_password = "Lowpr1v!" + suffix
    usernames = [f"test-bulk-user-{i}-{suffix}@example.test" for i in range(3)]

    # ──────────────────────── auth setup ────────────────────────
    openid = KeycloakOpenID(
        server_url=KEYCLOAK_URL,
        realm_name=TARGET_REALM,
        client_id=CLIENT_ID,
        client_secret_key=CLIENT_SECRET,
    )
    admin_token_payload = openid.token(grant_type="client_credentials")
    admin_token = admin_token_payload["access_token"]
    admin = KeycloakAdmin(
        server_url=KEYCLOAK_URL,
        realm_name=TARGET_REALM,
        token=admin_token_payload,
        verify=False,
    )

    created_role_id = None
    created_empty_role_id = None
    created_user_ids: list[str] = []
    created_lowpriv_id = None

    try:
        # ──────────────────────── fixture ────────────────────────
        admin.create_realm_role({"name": role_name})
        created_role_id = admin.get_realm_role(role_name)["id"]
        print(f"Created role {role_name} (id={created_role_id})")

        admin.create_realm_role({"name": empty_role_name})
        created_empty_role_id = admin.get_realm_role(empty_role_name)["id"]

        for username in usernames:
            uid = admin.create_user(
                {"username": username, "email": username, "enabled": True}
            )
            created_user_ids.append(uid)

        role_repr = admin.get_realm_role(role_name)
        admin.assign_realm_roles(user_id=created_user_ids[0], roles=[role_repr])
        admin.assign_realm_roles(user_id=created_user_ids[2], roles=[role_repr])

        created_lowpriv_id = admin.create_user(
            {
                "username": lowpriv_username,
                "email": lowpriv_username,
                "enabled": True,
                "emailVerified": True,
                "credentials": [
                    {
                        "type": "password",
                        "value": lowpriv_password,
                        "temporary": False,
                    }
                ],
            }
        )
        lowpriv_token = _password_token(
            TARGET_REALM,
            CLIENT_ID,
            lowpriv_username,
            lowpriv_password,
            client_secret=CLIENT_SECRET,
        )

        master_admin_token = _password_token(
            "master", "admin-cli", MASTER_ADMIN_USER, MASTER_ADMIN_PASS
        )

        print("Fixture ready: roles, users, low-priv token, master token")

        def post(body, *, raw_data=None, content_type="application/json"):
            return _post_with_token(
                admin_token, body, raw_data=raw_data, content_type=content_type
            )

        # ─────────────────────── happy paths ────────────────────────
        r = post({"role_id": created_role_id, "usernames": usernames})
        assert r.status_code == 200, r.text
        assert set(r.json()["members"]) == {usernames[0], usernames[2]}
        _ok("happy path: 2 of 3 usernames match")

        r = post({"role_id": created_role_id, "usernames": [usernames[1]]})
        assert r.status_code == 200 and r.json()["members"] == []
        _ok("subset with no match returns empty list")

        r = post({"role_id": created_role_id, "usernames": [usernames[0].upper()]})
        assert r.status_code == 200 and r.json()["members"] == [usernames[0]]
        _ok("case-insensitive lookup, canonical username returned")

        r = post({"role_id": created_role_id, "usernames": []})
        assert r.status_code == 200 and r.json()["members"] == []
        _ok("empty usernames list returns empty list")

        # Duplicates in input should not multiply rows in the response.
        r = post(
            {"role_id": created_role_id, "usernames": [usernames[0], usernames[0]]}
        )
        assert r.status_code == 200 and r.json()["members"] == [usernames[0]]
        _ok("duplicate input deduped to one row")

        r = post({"role_id": created_empty_role_id, "usernames": usernames})
        assert r.status_code == 200 and r.json()["members"] == []
        _ok("role with no members returns empty list")

        # Unknown fields in the body must be ignored (forward-compatibility).
        r = post(
            {
                "role_id": created_role_id,
                "usernames": usernames,
                "future_field": "ignore me",
            }
        )
        assert r.status_code == 200 and set(r.json()["members"]) == {
            usernames[0],
            usernames[2],
        }
        _ok("unknown body fields are ignored")

        # ───────────────────── input validation ─────────────────────
        r = post({"role_id": created_role_id})
        assert r.status_code == 400
        # Admin-REST error shape: structured body with "errorMessage" key.
        assert "errorMessage" in r.json()
        _ok("missing usernames field → 400 with errorMessage body")

        r = post({"usernames": usernames})
        assert r.status_code == 400
        _ok("missing role_id field → 400")

        r = post({})
        assert r.status_code == 400
        _ok("empty JSON object → 400")

        r = post({"role_id": None, "usernames": usernames})
        assert r.status_code == 400
        _ok("explicit null role_id → 400")

        r = post({"role_id": created_role_id, "usernames": None})
        assert r.status_code == 400
        _ok("explicit null usernames → 400")

        r = post(None, raw_data="")
        assert r.status_code == 400
        _ok("empty body → 400")

        r = post(None, raw_data="this is not json")
        assert r.status_code == 400
        _ok("non-JSON body → 400")

        r = post({"role_id": created_role_id, "usernames": "alice"})
        assert r.status_code == 400
        _ok("usernames as string (not list) → 400")

        # Null elements inside the list should be filtered out, not NPE.
        r = post(
            {
                "role_id": created_role_id,
                "usernames": [None, usernames[0], None, usernames[2]],
            }
        )
        assert r.status_code == 200 and set(r.json()["members"]) == {
            usernames[0],
            usernames[2],
        }
        _ok("null elements inside usernames are filtered, not NPE")

        # ───────────────────── size limit ─────────────────────
        oversized = [f"x{i}@example.test" for i in range(1001)]
        r = post({"role_id": created_role_id, "usernames": oversized})
        assert r.status_code == 400 and "1000" in r.text
        _ok("usernames length 1001 → 400 with cap hint")

        # 1000 exactly should be accepted (we just won't get any matches).
        ok_sized = [f"x{i}@example.test" for i in range(1000)]
        r = post({"role_id": created_role_id, "usernames": ok_sized})
        assert r.status_code == 200 and r.json()["members"] == []
        _ok("usernames length 1000 accepted at the cap")

        # ───────────────────── role validation ─────────────────────
        r = post(
            {
                "role_id": "00000000-0000-0000-0000-000000000000",
                "usernames": usernames,
            }
        )
        assert r.status_code == 404
        _ok("nonexistent role_id (zero UUID) → 404")

        r = post({"role_id": "not-a-uuid", "usernames": usernames})
        assert r.status_code == 404
        _ok("non-UUID role_id → 404, not 500")

        r = post({"role_id": "", "usernames": usernames})
        assert r.status_code == 404
        _ok("empty-string role_id → 404")

        # Cross-realm safety: ask for a master-realm role id while hitting
        # the messages-realm endpoint. The defensive containerId check
        # rejects this even if Keycloak's per-realm getRoleById ever leaked.
        master_admin_client = KeycloakAdmin(
            server_url=KEYCLOAK_URL,
            username=MASTER_ADMIN_USER,
            password=MASTER_ADMIN_PASS,
            realm_name="master",
            user_realm_name="master",
            verify=False,
        )
        master_admin_role_id = master_admin_client.get_realm_role("admin")["id"]
        r = post({"role_id": master_admin_role_id, "usernames": usernames})
        assert r.status_code == 404
        _ok("cross-realm role_id (master 'admin' role) → 404")

        # ────────────────────── authentication ──────────────────────
        r = _post_with_token(None, {"role_id": created_role_id, "usernames": usernames})
        assert r.status_code == 401
        _ok("no Authorization header → 401")

        r = _post_with_token(
            "not.a.real.token",
            {"role_id": created_role_id, "usernames": usernames},
        )
        assert r.status_code == 401
        _ok("garbage bearer token → 401")

        # An access token issued by /realms/master should not authenticate
        # against /realms/messages/...; signature/issuer mismatch → 401.
        r = _post_with_token(
            master_admin_token,
            {"role_id": created_role_id, "usernames": usernames},
        )
        assert r.status_code == 401
        _ok("master-realm token on messages endpoint → 401")

        # ─────────────────────── authorization ──────────────────────
        # A regular user with a valid token but no realm-management roles
        # must be rejected with 403, not allowed through.
        r = _post_with_token(
            lowpriv_token,
            {"role_id": created_role_id, "usernames": usernames},
        )
        assert r.status_code == 403, (
            f"low-priv user: expected 403, got {r.status_code}: {r.text}"
        )
        _ok("authenticated low-privilege user → 403")

        # ─────────────────────── SQL injection ──────────────────────
        # role_id is bound by name; no string concatenation. A SQL-meta
        # value just fails the role lookup → 404.
        r = post({"role_id": "' OR 1=1 --", "usernames": usernames})
        assert r.status_code == 404
        _ok("SQL-meta role_id → 404, no injection")

        # usernames are bound by name too. Even a username containing
        # statement terminators and a DROP cannot escape the parameter.
        evil = "alice'; DROP TABLE USER_ENTITY; --@example.test"
        r = post({"role_id": created_role_id, "usernames": [evil]})
        assert r.status_code == 200 and r.json()["members"] == []
        _ok("SQL-meta username → 200, no injection")

        # Verify the tables are still alive by re-running a known-good call.
        r = post({"role_id": created_role_id, "usernames": [usernames[0]]})
        assert r.status_code == 200 and r.json()["members"] == [usernames[0]]
        _ok("tables intact after injection attempts")

        # ──────────────────────── HTTP semantics ────────────────────
        r = requests.get(
            ENDPOINT_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            verify=False,
            timeout=10,
        )
        assert r.status_code == 405, f"GET should be 405, got {r.status_code}"
        _ok("GET on POST-only endpoint → 405")

        r = post(
            {"role_id": created_role_id, "usernames": usernames},
            content_type="text/plain",
        )
        # Jackson + JAX-RS reject the wrong media type with 415.
        assert r.status_code in (400, 415), (
            f"wrong content-type: expected 400/415, got {r.status_code}"
        )
        _ok("wrong Content-Type rejected (415/400)")

        # Response always carries a "members" key, even when empty.
        r = post({"role_id": created_empty_role_id, "usernames": usernames})
        assert "members" in r.json()
        assert r.headers.get("content-type", "").startswith("application/json")
        _ok("response shape: members key present, JSON content-type")

        print("\nAll assertions passed.")
        return 0

    finally:
        for uid in created_user_ids + (
            [created_lowpriv_id] if created_lowpriv_id else []
        ):
            try:
                admin.delete_user(uid)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"cleanup: failed to delete user {uid}: {exc}", file=sys.stderr)
        for role in (role_name, empty_role_name):
            try:
                admin.delete_realm_role(role)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(
                    f"cleanup: failed to delete role {role}: {exc}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    sys.exit(main())

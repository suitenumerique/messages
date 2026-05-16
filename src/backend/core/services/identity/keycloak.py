"""Keycloak identity management integration."""

import json
import logging
import secrets
import time

from django.conf import settings

from keycloak import KeycloakAdmin, KeycloakOpenID
from keycloak.exceptions import KeycloakError

from core.models import Mailbox, MailDomain

logger = logging.getLogger(__name__)

# Reuse a single admin client across calls so each one doesn't pay a fresh
# OIDC client_credentials round trip. The grant has no refresh token, so we
# cache against the access token's own ``expires_in`` and refresh slightly
# before actual expiry to give any in-flight operation headroom. A handful
# of concurrent misses re-fetch — acceptable, the only cost is a duplicate
# token issuance.
_TOKEN_EXPIRY_SAFETY_MARGIN = 30  # seconds shaved off advertised expires_in
_TOKEN_MIN_TTL = 10  # floor so a near-zero expires_in still caches briefly
_admin_client_cache: dict = {"client": None, "expires_at": 0.0}


def get_keycloak_admin_client():
    """Return a KeycloakAdmin client backed by the rest-api service account.

    Cached at module level until the access token is close to expiry. The
    cache is process-local; under thread contention a few concurrent misses
    may each fetch a token, which is harmless.
    """
    now = time.monotonic()
    cached = _admin_client_cache["client"]
    if cached is not None and _admin_client_cache["expires_at"] > now:
        return cached

    keycloak_openid = KeycloakOpenID(
        server_url=settings.KEYCLOAK_URL,
        realm_name=settings.KEYCLOAK_REALM,
        client_id=settings.KEYCLOAK_CLIENT_ID,
        client_secret_key=settings.KEYCLOAK_CLIENT_SECRET,
    )
    token = keycloak_openid.token(grant_type="client_credentials")

    keycloak_admin = KeycloakAdmin(
        server_url=settings.KEYCLOAK_URL,
        realm_name=settings.KEYCLOAK_REALM,
        verify=True,
        token=token,
    )

    expires_in = int(token.get("expires_in", 60))
    ttl = max(expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN, _TOKEN_MIN_TTL)
    _admin_client_cache["client"] = keycloak_admin
    _admin_client_cache["expires_at"] = now + ttl
    return keycloak_admin


def sync_maildomain_to_keycloak_group(maildomain):
    """
    Sync a MailDomain to Keycloak as a group.
    Creates the group if it doesn't exist and updates its attributes.
    """
    if not maildomain.identity_sync:
        logger.debug(
            "Skipping Keycloak sync for MailDomain %s - identity_sync disabled",
            maildomain.name,
        )
        return None

    try:
        keycloak_admin = get_keycloak_admin_client()
        group_path = f"{settings.KEYCLOAK_GROUP_PATH_PREFIX}{maildomain.name}"
        group_name = group_path.rsplit("/", maxsplit=1)[-1]
        parent_path = group_path.rsplit("/", maxsplit=1)[0]

        # Check if group exists
        existing_group = keycloak_admin.get_group_by_path(group_path)
        if existing_group and "error" in existing_group:
            existing_group = None

        # Prepare group attributes
        group_attributes = {
            "maildomain_id": [str(maildomain.id)],
            "maildomain_name": [maildomain.name],
        }

        # Add custom attributes
        if maildomain.custom_attributes:
            for key, value in maildomain.custom_attributes.items():
                # Do not send keys starting with _ to Keycloak
                if key.startswith("_"):
                    continue
                # Ensure values are lists (Keycloak requirement)
                if isinstance(value, list):
                    group_attributes[key] = value
                else:
                    group_attributes[key] = [str(value)]

        if existing_group:
            # Update existing group
            group_id = existing_group["id"]
            keycloak_admin.update_group(
                group_id=group_id,
                payload={
                    "name": group_name,
                    "attributes": group_attributes,
                },
            )
            logger.info(
                "Updated Keycloak group %s for MailDomain %s",
                group_name,
                maildomain.name,
            )
        else:
            # Create new group
            group_payload = {
                "name": group_name,
                "attributes": group_attributes,
            }
            parent_id = None
            if parent_path:
                parent_group = keycloak_admin.get_group_by_path(parent_path)
                if parent_group and "error" not in parent_group:
                    parent_id = parent_group["id"]

            group_id = keycloak_admin.create_group(
                payload=group_payload, parent=parent_id
            )
            logger.info(
                "Created Keycloak group %s for MailDomain %s",
                group_name,
                maildomain.name,
            )

        return group_id

    except KeycloakError as e:
        logger.error("Keycloak error syncing MailDomain %s: %s", maildomain.name, e)
        raise


def sync_mailbox_to_keycloak_user(mailbox):
    """
    Sync a Mailbox to Keycloak as a user in its maildomain group.
    Creates the user if it doesn't exist and adds them to the appropriate group.
    Uses email as username in Keycloak.
    """
    if not mailbox.domain.identity_sync or not mailbox.is_identity:
        return None

    try:
        keycloak_admin = get_keycloak_admin_client()
        email = str(mailbox)  # e.g., "user@domain.com"
        username = email  # Use email as username

        # Retrieve the mailbox initial user and get its custom attributes
        owner_mailbox_access = mailbox.accesses.order_by("created_at").first()
        user_custom_attributes = {}
        if owner_mailbox_access:
            local_user = owner_mailbox_access.user
            user_custom_attributes = local_user.custom_attributes

        # Check if user exists
        existing_users = keycloak_admin.get_users({"username": username})
        user_id = None

        if existing_users:
            user_id = existing_users[0]["id"]

        # Prepare user attributes
        user_attributes = {
            "mailbox_id": [str(mailbox.id)],
            "maildomain_id": [str(mailbox.domain.id)],
            "local_part": [mailbox.local_part],
            "domain_name": [mailbox.domain.name],
        }

        for key, value in user_custom_attributes.items():
            # Do not send keys starting with _ to Keycloak
            if key.startswith("_"):
                continue
            if isinstance(value, list):
                user_attributes[key] = value
            else:
                user_attributes[key] = [str(value)]

        # Get contact name if available
        first_name = ""
        last_name = ""
        if mailbox.contact and mailbox.contact.name:
            name_parts = mailbox.contact.name.split(" ", 1)
            first_name = name_parts[0]
            if len(name_parts) > 1:
                last_name = name_parts[1]

        if user_id:
            # Update existing user
            keycloak_admin.update_user(
                user_id=user_id,
                payload={
                    "username": username,
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "enabled": True,
                    "attributes": user_attributes,
                },
            )
            logger.info("Updated Keycloak user %s for Mailbox %s", username, mailbox)
        else:
            # Create new user
            user_payload = {
                "username": username,
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "enabled": True,
                "emailVerified": True,
                "attributes": user_attributes,
            }
            user_id = keycloak_admin.create_user(payload=user_payload)
            logger.info("Created Keycloak user %s for Mailbox %s", username, mailbox)

        # Add user to maildomain group
        group_path = f"{settings.KEYCLOAK_GROUP_PATH_PREFIX}{mailbox.domain.name}"
        group_name = group_path.rsplit("/", maxsplit=1)[-1]

        def list_groups_and_subgroups():
            groups = keycloak_admin.get_groups({"search": group_name})

            for group in groups:
                yield group
                yield from group.get("subGroups") or []

        for group in list_groups_and_subgroups():
            if group.get("name") == group_name:
                group_id = group["id"]

                # Check if user is already in the group
                user_groups = keycloak_admin.get_user_groups(user_id)
                is_member = any(g["id"] == group_id for g in user_groups)

                if not is_member:
                    keycloak_admin.group_user_add(user_id, group_id)
                    logger.info("Added user %s to group %s", username, group_name)
                break
        else:
            logger.warning("Group %s not found for user %s", group_name, username)

        return user_id

    except KeycloakError as e:
        logger.error("Keycloak error syncing Mailbox %s: %s", mailbox, e)
        raise


def list_keycloak_users(limit=100):
    """
    List all users in the Keycloak realm.
    """
    try:
        keycloak_admin = get_keycloak_admin_client()
        users = keycloak_admin.get_users({"max": limit})
        return users
    except KeycloakError as e:
        logger.error("Keycloak error listing users: %s", e)
        raise


def reset_keycloak_user_password(username, new_password=None):
    """
    Reset a user's password in Keycloak with a one-time new password.

    Also re-enables the account if it was disabled and clears any brute-force
    lockout, so admins can recover users who got locked out after too many
    failed login attempts in one operation.
    """
    if not new_password:
        new_password = generate_password()

    try:
        keycloak_admin = get_keycloak_admin_client()

        # Find user by username (which is email)
        users = keycloak_admin.get_users({"username": username})
        if not users:
            raise ValueError(f'User with username "{username}" not found.')

        user = users[0]
        user_id = user["id"]

        # If the account was disabled (e.g. by an admin), re-enable it before
        # the new password becomes useful.
        if not user.get("enabled", True):
            keycloak_admin.update_user(user_id=user_id, payload={"enabled": True})
            logger.info("Re-enabled Keycloak user: %s", username)

        # Clear any brute-force lockout. This is a no-op when there are no
        # recorded failures, and ensures a fresh password is immediately usable.
        try:
            keycloak_admin.clear_bruteforce_attempts_for_user(user_id=user_id)
        except KeycloakError as e:
            # Don't fail the whole password reset if the brute-force clear
            # endpoint hiccups (e.g. policy disabled in some realms).
            logger.warning(
                "Could not clear brute-force attempts for %s: %s", username, e
            )

        # Set new temporary password
        keycloak_admin.set_user_password(
            user_id=user_id, password=new_password, temporary=True
        )

        logger.info("Reset password for Keycloak user: %s", username)
        return new_password

    except KeycloakError as e:
        # Deliberately do not log the exception's body/message: while Keycloak
        # itself does not echo the request password back in error responses,
        # ``KeycloakError.__str__`` and ``response_body`` include the raw HTTP
        # response, which we keep out of logs as defense-in-depth on a path
        # that handles a brand-new password.
        response_code = getattr(e, "response_code", None)
        logger.error(
            "Keycloak error resetting password for %s (status=%s)",
            username,
            response_code,
        )
        raise


def resync_all_mailboxes_to_keycloak():
    """
    Resync all mailboxes with identity_sync enabled to Keycloak.
    """
    synced_domains = 0
    synced_mailboxes = 0

    # Get all domains with identity_sync enabled
    domains_with_sync = MailDomain.objects.filter(identity_sync=True)

    for domain in domains_with_sync:
        sync_maildomain_to_keycloak_group(domain)
        synced_domains += 1
        logger.info("Synced domain: %s", domain.name)

    # Get all mailboxes in domains with identity_sync enabled
    mailboxes_to_sync = Mailbox.objects.filter(domain__identity_sync=True)

    for mailbox in mailboxes_to_sync:
        sync_mailbox_to_keycloak_user(mailbox)
        synced_mailboxes += 1
        logger.info("Synced mailbox: %s", mailbox)

    return {"synced_domains": synced_domains, "synced_mailboxes": synced_mailboxes}


def generate_password(length=12):
    """
    Generate a secure random password with at least one uppercase, one lowercase, one digit, and one special character.
    """
    if length < 12:
        raise ValueError(
            "Password length must be at least 12 to satisfy all requirements."
        )

    _upper = "ABCDEFGHJKLMNPQRTUVWXYZ"
    _lower = "abcdefghijkmnopqrstuvwxyz"
    _digits = "2346789"
    _special = "!@#$%&*?"

    # Ensure at least one of each required character type
    password_chars = [
        secrets.choice(_upper),
        secrets.choice(_lower),
        secrets.choice(_digits),
        secrets.choice(_special),
    ]
    # Fill the rest of the password length with random choices
    password_chars += [
        secrets.choice(_upper + _lower + _digits + _special) for _ in range(length - 4)
    ]
    # Shuffle to avoid predictable positions
    secrets.SystemRandom().shuffle(password_chars)
    return "".join(password_chars)


def _get_keycloak_user_id(username):
    """Look up a Keycloak user id by username, raising ValueError if absent
    or ambiguous.

    ``exact=True`` keeps Keycloak from substring-matching the username — by
    default ``/users?username=foo`` returns anything containing ``foo``,
    which at scale can surface the wrong user. We additionally require
    exactly one result so an unexpected collision raises instead of
    silently picking ``users[0]``.
    """
    keycloak_admin = get_keycloak_admin_client()
    users = keycloak_admin.get_users({"username": username, "exact": True})
    if not users:
        raise ValueError(f'User with username "{username}" not found.')
    if len(users) > 1:
        raise ValueError(
            f'Ambiguous username "{username}": {len(users)} Keycloak users matched.'
        )
    return keycloak_admin, users[0]["id"]


def has_realm_role(username, role_id):
    """Return True if the Keycloak user has the realm role with this id assigned."""
    keycloak_admin, user_id = _get_keycloak_user_id(username)
    user_roles = keycloak_admin.get_realm_roles_of_user(user_id=user_id)
    return any(role.get("id") == role_id for role in user_roles)


def is_mandatory_totp_enabled():
    """All three settings required for the mandatory TOTP feature are present."""
    return bool(
        settings.FEATURE_MAILDOMAIN_MANAGE_TOTP
        and settings.KEYCLOAK_TOTP_ROLE_ID
        and settings.IDENTITY_PROVIDER == "keycloak"
    )


def batch_realm_role_membership(usernames, role_id):
    """Return ``{username: bool}`` indicating which of ``usernames`` hold
    the realm role with id ``role_id``.

    Routed through the ``bulk-role-membership`` custom Keycloak provider
    (see ``src/keycloak/bulk-role-membership``) which answers the whole
    page in one indexed DB query inside Keycloak. Keycloak's stock admin
    API has no equivalent: every alternative is either O(N) round trips
    or fetches the full role membership list.

    Usernames in the response are compared case-insensitively (Keycloak's
    canonical form is lowercased), so the returned dict is keyed back by
    the input strings exactly as provided.
    """
    if not usernames:
        return {}

    keycloak_admin = get_keycloak_admin_client()
    response = keycloak_admin.connection.raw_post(
        f"/realms/{settings.KEYCLOAK_REALM}/bulk-role-membership/check",
        data=json.dumps({"role_id": role_id, "usernames": list(usernames)}),
    )
    response.raise_for_status()
    matched = {m.lower() for m in response.json().get("members", [])}
    return {u: u.lower() in matched for u in usernames}


def set_realm_role(username, role_id, *, assigned):
    """Assign or remove a realm role (looked up by id) for the Keycloak user.

    Idempotent: if the role is already in the desired state, this is a no-op.
    """
    keycloak_admin, user_id = _get_keycloak_user_id(username)
    role = keycloak_admin.get_realm_role_by_id(role_id=role_id)
    if not role:
        raise ValueError(f'Realm role with id "{role_id}" not found.')

    user_roles = keycloak_admin.get_realm_roles_of_user(user_id=user_id)
    currently_assigned = any(r.get("id") == role_id for r in user_roles)

    if assigned and not currently_assigned:
        keycloak_admin.assign_realm_roles(user_id=user_id, roles=[role])
        logger.info(
            "Assigned realm role %s to Keycloak user %s", role.get("name"), username
        )
    elif not assigned and currently_assigned:
        keycloak_admin.delete_realm_roles_of_user(user_id=user_id, roles=[role])
        logger.info(
            "Removed realm role %s from Keycloak user %s", role.get("name"), username
        )


def reset_keycloak_user_totp(username):
    """Reset a user's TOTP enrollment.

    Deletes any OTP credentials they have on file and registers
    ``CONFIGURE_TOTP`` as a required action so they re-enroll on next login.
    """
    keycloak_admin, user_id = _get_keycloak_user_id(username)

    credentials = keycloak_admin.get_credentials(user_id=user_id) or []
    deleted = 0
    for credential in credentials:
        # Keycloak labels OTP creds with type "otp"; covers TOTP & HOTP.
        if (credential.get("type") or "").lower() == "otp":
            keycloak_admin.delete_credential(
                user_id=user_id, credential_id=credential["id"]
            )
            deleted += 1

    user = keycloak_admin.get_user(user_id=user_id) or {}
    required_actions = list(user.get("requiredActions") or [])
    if "CONFIGURE_TOTP" not in required_actions:
        required_actions.append("CONFIGURE_TOTP")
        keycloak_admin.update_user(
            user_id=user_id, payload={"requiredActions": required_actions}
        )

    logger.info(
        "Reset TOTP for Keycloak user %s (removed %d OTP credentials)",
        username,
        deleted,
    )
    return {"removed_credentials": deleted}

"""Keycloak identity management integration."""

import logging
import secrets
import string

from django.conf import settings

from keycloak import KeycloakAdmin, KeycloakOpenID
from keycloak.exceptions import KeycloakError

from core.models import Mailbox, MailDomain

logger = logging.getLogger(__name__)


def get_keycloak_admin_client():
    """
    Get a KeycloakAdmin client using the rest-api service account.
    """

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
        group_name = f"maildomain-{maildomain.name}"

        # Check if group exists
        existing_groups = keycloak_admin.get_groups({"search": group_name})
        group_id = None

        for group in existing_groups:
            if group.get("name") == group_name:
                group_id = group["id"]
                break

        # Prepare group attributes
        group_attributes = {
            "maildomain_id": [str(maildomain.id)],
            "maildomain_name": [maildomain.name],
        }

        # Add custom metadata from identity_group_metadata
        if maildomain.identity_group_metadata:
            for key, value in maildomain.identity_group_metadata.items():
                # Ensure values are lists (Keycloak requirement)
                if isinstance(value, list):
                    group_attributes[key] = value
                else:
                    group_attributes[key] = [str(value)]

        if group_id:
            # Update existing group
            keycloak_admin.update_group(
                group_id=group_id,
                payload={"name": group_name, "attributes": group_attributes},
            )
            logger.info(
                "Updated Keycloak group %s for MailDomain %s",
                group_name,
                maildomain.name,
            )
        else:
            # Create new group
            group_payload = {"name": group_name, "attributes": group_attributes}
            group_id = keycloak_admin.create_group(payload=group_payload)
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
    if not mailbox.domain.identity_sync:
        logger.debug(
            "Skipping Keycloak sync for Mailbox %s - identity_sync disabled on domain",
            mailbox,
        )
        return None

    try:
        keycloak_admin = get_keycloak_admin_client()
        email = str(mailbox)  # e.g., "user@domain.com"
        username = email  # Use email as username

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
        group_name = f"maildomain-{mailbox.domain.name}"
        groups = keycloak_admin.get_groups({"search": group_name})

        for group in groups:
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

        # Set new temporary password
        keycloak_admin.set_user_password(
            user_id=user_id, password=new_password, temporary=True
        )

        logger.info("Reset password for Keycloak user: %s", username)
        return new_password

    except KeycloakError as e:
        logger.error("Keycloak error resetting password for %s: %s", username, e)
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
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(length))
    return password

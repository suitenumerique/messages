"""Tests for the Mailbox permissions system based on get_abilities."""
# pylint: disable=redefined-outer-name,unused-argument

from unittest.mock import patch

from django.test import override_settings

import pytest

from core import models
from core.factories import MailboxFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def mailbox():
    """Create a test mailbox."""
    return MailboxFactory()


class TestModelMailbox:
    """Tests for Mailbox model."""

    def test_models_mailbox_str(self, mailbox):
        """String representation of a mailbox should be the concatenation of the local part and domain name."""
        assert str(mailbox) == f"{mailbox.local_part}@{mailbox.domain.name}"

    @override_settings(IDENTITY_PROVIDER="keycloak")
    def test_models_mailbox_can_reset_password_true(self, mailbox):
        """
        A mailbox password should be resetable
        when it is an identity, domain sync is enabled and the identity provider is Keycloak.
        """
        mailbox.is_identity = True
        mailbox.domain.identity_sync = True

        assert mailbox.can_reset_password is True

    @override_settings(IDENTITY_PROVIDER="keycloak")
    def test_models_mailbox_can_reset_password_false_when_not_identity(self, mailbox):
        """
        A mailbox password should not be resetable when the mailbox is not an identity one.
        """
        mailbox.is_identity = False
        mailbox.domain.identity_sync = True

        assert mailbox.can_reset_password is False

    @override_settings(IDENTITY_PROVIDER="something-else")
    def test_models_mailbox_can_reset_password_false_when_not_keycloak(self, mailbox):
        """
        A mailbox password should not be resetable when the configured identity provider is not Keycloak.
        """
        mailbox.is_identity = True
        mailbox.domain.identity_sync = True

        assert mailbox.can_reset_password is False

    @override_settings(IDENTITY_PROVIDER="keycloak")
    def test_models_mailbox_can_reset_password_false_when_domain_not_synced(
        self, mailbox, settings
    ):
        """
        A mailbox password should not be resetable when the domain identity sync is disabled.
        """
        mailbox.is_identity = True
        mailbox.domain.identity_sync = False

        assert mailbox.can_reset_password is False

    @override_settings(IDENTITY_PROVIDER="keycloak")
    @patch("core.services.identity.keycloak.reset_keycloak_user_password")
    def test_models_mailbox_reset_password_calls_keycloak_when_allowed(
        self, mock_reset_password, mailbox, settings
    ):
        """reset_password should call keycloak service when allowed and return its result."""
        mailbox.is_identity = True
        mailbox.domain.identity_sync = True
        settings.IDENTITY_PROVIDER = "keycloak"

        mailbox.reset_password()
        mock_reset_password.assert_called_once_with(str(mailbox))

    @patch("core.services.identity.keycloak.reset_keycloak_user_password")
    def test_models_mailbox_reset_password_noop_when_not_allowed(
        self, mock_reset_password, mailbox, settings
    ):
        """reset_password should not call keycloak when not allowed and return None."""
        mailbox.is_identity = True
        mailbox.domain.identity_sync = False  # Not synced → not allowed
        settings.IDENTITY_PROVIDER = "keycloak"

        mock_reset_password.assert_not_called()


class TestMailboxModelAbilities:
    """Test the get_abilities methods on Mailbox models."""

    def test_mailbox_get_abilities_no_access(self, user, mailbox):
        """Test Mailbox.get_abilities when user has no access."""
        abilities = mailbox.get_abilities(user)

        assert abilities["get"] is False
        assert abilities["patch"] is False
        assert abilities["put"] is False
        assert abilities["post"] is False
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["view_messages"] is False
        assert abilities["send_messages"] is False
        assert abilities["manage_labels"] is False

    def test_mailbox_get_abilities_viewer(self, user, mailbox):
        """Test Mailbox.get_abilities when user has viewer access."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.VIEWER,
        )

        abilities = mailbox.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is False
        assert abilities["put"] is False
        assert abilities["post"] is False
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is False
        assert abilities["manage_labels"] is False

    def test_mailbox_get_abilities_editor(self, user, mailbox):
        """Test Mailbox.get_abilities when user has editor access."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.EDITOR,
        )

        abilities = mailbox.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is False
        assert abilities["manage_labels"] is True

    def test_mailbox_get_abilities_admin(self, user, mailbox):
        """Test Mailbox.get_abilities when user has admin access."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.ADMIN,
        )

        abilities = mailbox.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is True
        assert abilities["manage_accesses"] is True
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is True
        assert abilities["manage_labels"] is True

    def test_mailbox_get_abilities_sender(self, user, mailbox):
        """Test Mailbox.get_abilities when user has sender access."""
        models.MailboxAccess.objects.create(
            mailbox=mailbox,
            user=user,
            role=models.MailboxRoleChoices.SENDER,
        )

        abilities = mailbox.get_abilities(user)

        assert abilities["get"] is True
        assert abilities["patch"] is True
        assert abilities["put"] is True
        assert abilities["post"] is True
        assert abilities["delete"] is False
        assert abilities["manage_accesses"] is False
        assert abilities["view_messages"] is True
        assert abilities["send_messages"] is True
        assert abilities["manage_labels"] is True

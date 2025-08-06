"""Tests for the Mailbox permissions system based on get_abilities."""
# pylint: disable=redefined-outer-name,unused-argument

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

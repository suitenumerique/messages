"""Tests for the import-run API (`/imports/`, Channels with type=import)."""

# pylint: disable=redefined-outer-name, unused-argument

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.services.importer.channel import (
    create_import_channel,
    mark_finished,
    mark_started,
)


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def mailbox(user):
    """A mailbox the user can edit (EDITOR role)."""
    mailbox = factories.MailboxFactory()
    mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.EDITOR)
    return mailbox


def _make_import(mailbox, user, source=enums.ImportSource.MBOX):
    return create_import_channel(recipient=mailbox, user=user, source_type=source.value)


@pytest.mark.django_db
class TestImportList:
    def test_lists_only_accessible_imports(self, api_client, mailbox, user):
        mine = _make_import(mailbox, user)
        # An import on a mailbox the user cannot access must not show up.
        other_mailbox = factories.MailboxFactory()
        _make_import(other_mailbox, factories.UserFactory())

        response = api_client.get(reverse("imports-list"))
        assert response.status_code == status.HTTP_200_OK
        ids = [row["id"] for row in response.data]
        assert ids == [str(mine.id)]

    def test_viewer_role_cannot_see_imports(self, api_client, user):
        mailbox = factories.MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.VIEWER)
        _make_import(mailbox, user)

        response = api_client.get(reverse("imports-list"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_superuser_sees_all_imports(self):
        superuser = factories.UserFactory(is_superuser=True)
        client = APIClient()
        client.force_authenticate(user=superuser)
        imp = _make_import(factories.MailboxFactory(), factories.UserFactory())

        response = client.get(reverse("imports-list"))
        assert response.status_code == status.HTTP_200_OK
        assert str(imp.id) in [row["id"] for row in response.data]


@pytest.mark.django_db
class TestImportRetrieve:
    def test_retrieve_exposes_run_state(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        mark_started(channel.id, total_messages=4)
        mark_finished(
            channel.id,
            status=enums.ImportStatus.COMPLETED.value,
            success_count=3,
            failure_count=1,
            total_messages=4,
        )

        response = api_client.get(reverse("imports-detail", kwargs={"pk": channel.id}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == enums.ImportStatus.COMPLETED.value
        assert response.data["source_type"] == enums.ImportSource.MBOX.value
        assert response.data["total_messages"] == 4
        assert response.data["success_count"] == 3
        assert response.data["failure_count"] == 1
        assert response.data["progress"] == 100.0

    def test_retrieve_no_access_returns_404(self, api_client):
        channel = _make_import(factories.MailboxFactory(), factories.UserFactory())
        response = api_client.get(reverse("imports-detail", kwargs={"pk": channel.id}))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestImportCancel:
    def test_cancel_deletes_messages(self, api_client, mailbox, user):
        channel = _make_import(mailbox, user)
        contact = factories.ContactFactory(mailbox=mailbox)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
        factories.MessageFactory(thread=thread, sender=contact, channel=channel)

        response = api_client.post(reverse("imports-cancel", kwargs={"pk": channel.id}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["messages_deleted"] == 1
        assert not models.Message.objects.filter(channel=channel).exists()
        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_cancel_no_access_returns_404(self, api_client):
        channel = _make_import(factories.MailboxFactory(), factories.UserFactory())
        response = api_client.post(reverse("imports-cancel", kwargs={"pk": channel.id}))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestImportChannelsExcludedFromIntegrations:
    def test_import_channel_absent_from_mailbox_channels(self, api_client, user):
        mailbox = factories.MailboxFactory()
        mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
        widget = factories.ChannelFactory(mailbox=mailbox, type="widget")
        import_channel = _make_import(mailbox, user)

        url = reverse("mailbox-channels-list", kwargs={"mailbox_id": mailbox.id})
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        ids = [row["id"] for row in response.data]
        assert str(widget.id) in ids
        assert str(import_channel.id) not in ids

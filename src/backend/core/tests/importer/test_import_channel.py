"""Tests for grouping an import under a Channel (axis 1).

Covers the ``core.services.importer.channel`` helpers: channel creation,
race-free state updates, message grouping via ``deliver_inbound_message``,
and cancellation (message deletion + orphan-thread cleanup).

These deliberately avoid the heavyweight import tasks (and their eager
OpenSearch reindex flush): a plain ``deliver_inbound_message`` only enqueues
a reindex into the pending set, so no live OpenSearch call happens here.
"""

# pylint: disable=redefined-outer-name, unused-argument

import pytest
from jmap_email import parse_email

from core import enums, factories, models
from core.mda.inbound import deliver_inbound_message
from core.services.importer.channel import (
    cancel_import,
    create_import_channel,
    get_import_channel,
    mark_finished,
    mark_started,
    scrub_import_credentials,
)


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def mailbox():
    return factories.MailboxFactory()


@pytest.mark.django_db
class TestCreateImportChannel:
    """``create_import_channel`` builds a mailbox-scoped import channel."""

    def test_file_import_channel(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.MBOX.value,
            file_key="user/archive.mbox",
            name="Import archive.mbox",
        )
        assert channel.type == enums.ChannelTypes.IMPORT.value
        assert channel.scope_level == enums.ChannelScopeLevel.MAILBOX
        assert channel.mailbox_id == mailbox.id
        assert channel.user_id == user.id
        run = channel.settings["import"]
        assert run["status"] == enums.ImportStatus.PENDING.value
        assert run["source_type"] == enums.ImportSource.MBOX.value
        assert run["file_key"] == "user/archive.mbox"
        assert run["success_count"] == 0
        assert channel.encrypted_settings == {}

    def test_imap_import_channel_stores_credentials(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u@example.com", "password": "secret"},
        )
        assert channel.encrypted_settings["imap"]["password"] == "secret"
        # Reload from DB to confirm the encrypted field round-trips.
        channel.refresh_from_db()
        assert channel.encrypted_settings["imap"]["username"] == "u@example.com"


@pytest.mark.django_db
class TestImportState:
    """State writes land in settings without touching credentials."""

    def test_mark_started_then_finished(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
        )
        mark_started(channel.id, total_messages=10)
        channel.refresh_from_db()
        assert channel.settings["import"]["status"] == enums.ImportStatus.RUNNING.value
        assert channel.settings["import"]["total_messages"] == 10
        assert channel.settings["import"]["started_at"] is not None
        # Credentials survive a state write (no encrypted_settings rewrite).
        assert channel.encrypted_settings["imap"]["password"] == "p"

        mark_finished(
            channel.id,
            status=enums.ImportStatus.COMPLETED.value,
            success_count=9,
            failure_count=1,
            total_messages=10,
        )
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert run["status"] == enums.ImportStatus.COMPLETED.value
        assert run["success_count"] == 9
        assert run["failure_count"] == 1
        assert run["finished_at"] is not None

    def test_get_import_channel(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.EML.value
        )
        assert get_import_channel(str(channel.id)).id == channel.id
        assert get_import_channel(None) is None
        # A non-import channel is never returned by the import helper.
        other = factories.ChannelFactory(mailbox=mailbox, type="widget")
        assert get_import_channel(str(other.id)) is None


@pytest.mark.django_db
class TestGrouping:
    """Imported messages carry the import channel FK."""

    def test_deliver_stamps_channel_on_message(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.EML.value
        )
        raw = (
            "From: sender@example.com\r\n"
            f"To: {mailbox}\r\n"
            "Subject: Grouped\r\n"
            "Message-ID: <grouped-1@example.com>\r\n"
            "Date: Mon, 26 May 2025 20:13:44 +0200\r\n"
            "\r\n"
            "body"
        ).encode()
        parsed = parse_email(raw)

        assert deliver_inbound_message(
            str(mailbox), parsed, raw, is_import=True, channel=channel
        )

        message = models.Message.objects.get(channel=channel)
        assert message.subject == "Grouped"
        assert models.Message.objects.filter(channel=channel).count() == 1


@pytest.mark.django_db
class TestCancelImport:
    """Cancelling deletes the run's messages and cleans orphan threads."""

    def test_cancel_removes_messages_and_orphan_threads(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        contact = factories.ContactFactory(mailbox=mailbox)

        # Thread A: only an imported message -> deleted on cancel.
        thread_a = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_a)
        msg_a = factories.MessageFactory(
            thread=thread_a, sender=contact, channel=channel
        )

        # Thread B: an imported message + a pre-existing one -> survives.
        thread_b = factories.ThreadFactory()
        factories.ThreadAccessFactory(mailbox=mailbox, thread=thread_b)
        msg_b_import = factories.MessageFactory(
            thread=thread_b, sender=contact, channel=channel
        )
        msg_b_keep = factories.MessageFactory(thread=thread_b, sender=contact)

        summary = cancel_import(channel)

        assert summary["messages_deleted"] == 2
        assert summary["threads_deleted"] == 1
        assert not models.Message.objects.filter(id=msg_a.id).exists()
        assert not models.Message.objects.filter(id=msg_b_import.id).exists()
        assert models.Message.objects.filter(id=msg_b_keep.id).exists()
        assert not models.Thread.objects.filter(id=thread_a.id).exists()
        assert models.Thread.objects.filter(id=thread_b.id).exists()

        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_cancel_scrubs_credentials(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
        )
        cancel_import(channel)
        channel.refresh_from_db()
        assert channel.encrypted_settings == {}


@pytest.mark.django_db
class TestScrubImportCredentials:
    """``scrub_import_credentials`` drops stored creds once a run can't resume."""

    def test_scrubs_imap_credentials(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u@example.com", "password": "secret"},
        )
        assert channel.encrypted_settings.get("imap")

        scrub_import_credentials(channel.id)

        channel.refresh_from_db()
        assert channel.encrypted_settings == {}

    def test_noop_for_file_import(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.MBOX.value,
            file_key="archive.mbox",
        )
        # No credentials to begin with: scrubbing must not raise.
        scrub_import_credentials(channel.id)
        channel.refresh_from_db()
        assert channel.encrypted_settings == {}

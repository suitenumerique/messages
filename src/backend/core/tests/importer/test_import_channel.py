"""Tests for the import-run channel + Redis state + dedup fallback.

These avoid the heavyweight S3-backed import tasks: they exercise the
``core.services.importer.channel`` helpers, message grouping via
``deliver_inbound_message``, the blob-sha256 dedup fallback that makes
resume idempotent for header-less mail, and cancellation.
"""

# pylint: disable=redefined-outer-name, unused-argument

import pytest
from jmap_email import parse_email

from core import enums, factories, models
from core.mda.inbound import deliver_inbound_message
from core.services.importer import channel as channel_module
from core.services.importer.channel import (
    cancel_import,
    create_import_channel,
    enable_continuous,
    get_import_channel,
    mark_finished,
    mark_started,
    merged_state,
    pause_import,
    read_state,
    record_progress,
)


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def mailbox():
    return factories.MailboxFactory()


def _raw(
    mailbox, *, message_id="<m1@example.com>", subject="Hello", frm="s@example.com"
):
    headers = [
        f"From: {frm}",
        f"To: {mailbox}",
        f"Subject: {subject}",
    ]
    if message_id:
        headers.append(f"Message-ID: {message_id}")
    headers.append("Date: Mon, 26 May 2025 20:13:44 +0200")
    return ("\r\n".join(headers) + "\r\n\r\nbody").encode()


@pytest.mark.django_db
class TestCreateImportChannel:
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
        assert channel.is_active is True
        run = channel.settings["import"]
        assert run["source_type"] == enums.ImportSource.MBOX.value
        assert run["file_key"] == "user/archive.mbox"
        assert run["mode"] == enums.ImportMode.ONESHOT.value
        assert channel.encrypted_settings == {}

    def test_imap_channel_stores_credentials(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u@example.com", "password": "secret"},
        )
        channel.refresh_from_db()
        assert channel.encrypted_settings["imap"]["password"] == "secret"


@pytest.mark.django_db
class TestState:
    def test_started_progress_finished_flow(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        mark_started(channel.id, total=10)
        assert read_state(channel.id)["status"] == enums.ImportStatus.RUNNING.value

        record_progress(channel.id, success=4, failure=1, cursor=5)
        state = read_state(channel.id)
        assert (state["success"], state["failure"], state["cursor"]) == (4, 1, 5)

        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=9,
            failure=1,
            total=10,
        )
        channel.refresh_from_db()
        # Terminal marker is durable: is_active flipped + snapshot in settings.
        assert channel.is_active is False
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )
        assert channel.settings["import"]["success"] == 9

    def test_merged_state_survives_cache_eviction(self, mailbox, user):
        from django.core.cache import cache  # pylint: disable=import-outside-toplevel

        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.EML.value
        )
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=1,
            failure=0,
            total=1,
        )
        cache.clear()  # evict live Redis state
        merged = merged_state(channel)
        # Durable snapshot still reports the terminal state + counts.
        assert merged["status"] == enums.ImportStatus.COMPLETED.value
        assert merged["success"] == 1

    def test_continuous_run_stays_active_on_completion(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            mode=enums.ImportMode.CONTINUOUS.value,
            imap_credentials={"username": "u", "password": "p"},
        )
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=3,
            failure=0,
            total=3,
        )
        channel.refresh_from_db()
        # A continuous poll that "completed" stays enabled for the next poll —
        # and the completion is still durably recorded.
        assert channel.is_active is True
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )


@pytest.mark.django_db
class TestContinuousControls:
    def test_enable_continuous_rearms_a_finished_oneshot(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
        )
        # Finished oneshot: creds retained (not scrubbed), is_active False.
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=1,
            failure=0,
            total=1,
        )
        channel.refresh_from_db()
        assert channel.is_active is False
        assert channel.encrypted_settings.get("imap")  # creds kept

        enable_continuous(channel)
        channel.refresh_from_db()
        assert channel.is_active is True
        assert channel.settings["import"]["mode"] == enums.ImportMode.CONTINUOUS.value
        assert channel.last_used_at is None  # scheduler picks it up promptly

    def test_pause_disables_without_scrubbing_creds(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            mode=enums.ImportMode.CONTINUOUS.value,
            imap_credentials={"username": "u", "password": "p"},
        )
        pause_import(channel)
        channel.refresh_from_db()
        assert channel.is_active is False
        assert channel.encrypted_settings["imap"]["password"] == "p"


@pytest.mark.django_db
class TestGroupingAndDedup:
    def test_deliver_stamps_channel(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.EML.value
        )
        raw = _raw(mailbox)
        assert deliver_inbound_message(
            str(mailbox), parse_email(raw), raw, is_import=True, channel=channel
        )
        assert models.Message.objects.filter(channel=channel).count() == 1

    def test_message_id_dedup_on_reimport(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        raw = _raw(mailbox, message_id="<dup@example.com>")
        results = [
            deliver_inbound_message(
                str(mailbox), parse_email(raw), raw, is_import=True, channel=channel
            )
            for _ in range(2)
        ]
        # Second delivery deduped on Message-ID — and still reported as a
        # success to the caller (a dedup is not a failure).
        assert results == [True, True]
        assert models.Message.objects.filter(channel=channel).count() == 1

    def test_headerless_dedup_by_blob_sha256(self, mailbox, user):
        """A message with no Message-ID re-imports idempotently via its raw
        sha256 (== blob sha256) — the property resume relies on."""
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        raw = _raw(mailbox, message_id=None)
        results = [
            deliver_inbound_message(
                str(mailbox), parse_email(raw), raw, is_import=True, channel=channel
            )
            for _ in range(2)
        ]
        assert results == [True, True]
        assert models.Message.objects.filter(channel=channel).count() == 1


@pytest.mark.django_db
class TestLabelsAndDuplicateMerge:
    def test_compute_labels_and_flags_mapping(self):
        """Folder names map to flags (Sent/Drafts), \\Seen/\\Flagged map to
        read/starred, and unknown folders become plain labels."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.importer.labels import compute_labels_and_flags

        labels, flags = compute_labels_and_flags(
            {}, ["Sent", "ProjectX", "INBOX/Clients"], ["\\Seen", "\\Flagged"]
        )
        assert labels == {"ProjectX", "Clients"}  # INBOX/ prefix stripped
        assert flags["is_sender"] is True
        assert flags["is_unread"] is False  # \\Seen (and is_sender) => read
        assert flags["_starred"] is True

        labels, flags = compute_labels_and_flags({}, ["Drafts"], [])
        assert labels == set()
        assert flags["is_draft"] is True
        assert flags["is_unread"] is False  # drafts are never unread

    def test_duplicate_import_merges_labels_and_flags(self, mailbox, user):
        """Re-importing an existing message (same Message-ID) from another
        source must MERGE its labels/flags onto the existing message instead
        of dropping them — the classic overlapping-archives scenario."""
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.IMAP.value
        )
        raw = _raw(mailbox, message_id="<merge@example.com>")
        assert deliver_inbound_message(
            str(mailbox),
            parse_email(raw),
            raw,
            is_import=True,
            channel=channel,
            imap_labels=["Foo"],
        )
        assert deliver_inbound_message(
            str(mailbox),
            parse_email(raw),
            raw,
            is_import=True,
            channel=channel,
            imap_labels=["Bar"],
            imap_flags=["\\Seen"],
        )
        message = models.Message.objects.get(channel=channel)
        label_names = set(message.thread.labels.values_list("name", flat=True))
        assert {"Foo", "Bar"} <= label_names
        # The \\Seen flag from the duplicate marked the thread read for the
        # importing mailbox.
        access = models.ThreadAccess.objects.get(thread=message.thread, mailbox=mailbox)
        assert access.read_at is not None


@pytest.mark.django_db
class TestCancel:
    def test_cancel_deletes_messages_and_disables(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        raw = _raw(mailbox, message_id="<c1@example.com>")
        deliver_inbound_message(
            str(mailbox), parse_email(raw), raw, is_import=True, channel=channel
        )
        assert models.Message.objects.filter(channel=channel).exists()

        summary = cancel_import(channel)
        assert summary["messages_deleted"] == 1
        assert not models.Message.objects.filter(channel=channel).exists()
        channel.refresh_from_db()
        assert channel.is_active is False
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_cancel_keeps_imported_message_in_thread_with_a_reply(self, mailbox, user):
        """Cancelling undoes the *import* — but an imported message whose
        thread has since gathered real activity (a reply) anchors a live
        conversation and must survive the purge."""
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        raw_orig = _raw(mailbox, message_id="<orig@example.com>")
        deliver_inbound_message(
            str(mailbox),
            parse_email(raw_orig),
            raw_orig,
            is_import=True,
            channel=channel,
        )
        orig = models.Message.objects.get(channel=channel)
        raw_lone = _raw(mailbox, message_id="<lone@example.com>", subject="Other")
        deliver_inbound_message(
            str(mailbox),
            parse_email(raw_lone),
            raw_lone,
            is_import=True,
            channel=channel,
        )
        # A real (non-import) reply arrives in the first thread.
        reply = (
            "\r\n".join(
                [
                    "From: replier@example.com",
                    f"To: {mailbox}",
                    "Subject: Re: Hello",
                    "Message-ID: <reply@example.com>",
                    "In-Reply-To: <orig@example.com>",
                    "References: <orig@example.com>",
                    "Date: Tue, 27 May 2025 10:00:00 +0200",
                ]
            )
            + "\r\n\r\nreply body"
        ).encode()
        assert deliver_inbound_message(str(mailbox), parse_email(reply), reply)
        assert orig.thread.messages.count() == 2  # reply threaded onto the import

        summary = cancel_import(channel)

        assert summary["messages_deleted"] == 1  # the lone import
        assert summary["messages_kept"] == 1  # the replied-to anchor
        assert models.Message.objects.filter(id=orig.id).exists()
        assert orig.thread.messages.count() == 2
        # Idempotent: re-cancelling never comes back for the spared message.
        summary = cancel_import(channel)
        assert summary["messages_deleted"] == 0
        assert models.Message.objects.filter(id=orig.id).exists()

    def test_cancel_purges_thread_shared_only_with_another_import(self, mailbox, user):
        """A sibling import's messages are not "activity": cancelling each of
        two overlapping runs still removes all of their messages."""
        ch_a = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        ch_b = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        raw_a = _raw(mailbox, message_id="<a@example.com>")
        deliver_inbound_message(
            str(mailbox), parse_email(raw_a), raw_a, is_import=True, channel=ch_a
        )
        raw_b = (
            "\r\n".join(
                [
                    "From: s@example.com",
                    f"To: {mailbox}",
                    "Subject: Re: Hello",
                    "Message-ID: <b@example.com>",
                    "In-Reply-To: <a@example.com>",
                    "References: <a@example.com>",
                    "Date: Tue, 27 May 2025 10:00:00 +0200",
                ]
            )
            + "\r\n\r\nbody"
        ).encode()
        deliver_inbound_message(
            str(mailbox), parse_email(raw_b), raw_b, is_import=True, channel=ch_b
        )
        thread = models.Message.objects.get(channel=ch_a).thread
        assert thread.messages.count() == 2

        summary = cancel_import(ch_a)
        assert summary["messages_deleted"] == 1
        assert summary["messages_kept"] == 0
        thread.refresh_from_db()
        assert thread.messages.count() == 1  # B's message remains

        summary = cancel_import(ch_b)
        assert summary["messages_deleted"] == 1
        assert not models.Thread.objects.filter(id=thread.id).exists()

    def test_get_import_channel_type_guard(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.EML.value
        )
        assert get_import_channel(str(channel.id)).id == channel.id
        assert get_import_channel(None) is None
        other = factories.ChannelFactory(mailbox=mailbox, type="widget")
        assert get_import_channel(str(other.id)) is None

    def test_mark_cancelled_raises_cancel_flag(self, mailbox, user):
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        assert channel_module.is_cancel_requested(channel.id) is False
        channel_module.mark_cancelled(channel)
        assert channel_module.is_cancel_requested(channel.id) is True

    def test_cancel_flag_survives_state_dict_overwrite(self, mailbox, user):
        """The flag lives in its own key: a runner's read-modify-write of the
        state dict (racing the cancel) must not be able to clobber it."""
        from django.core.cache import cache  # pylint: disable=import-outside-toplevel

        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.MBOX.value
        )
        channel_module.request_cancel(channel.id)
        # Worst case: a whole-dict overwrite from a stale read.
        cache.set(channel_module._state_key(channel.id), {"success": 3}, timeout=60)
        assert channel_module.is_cancel_requested(channel.id) is True

    def test_enable_continuous_clears_stale_cancel_flag(self, mailbox, user):
        """Re-arming a cancelled import must not insta-cancel the new run: the
        cancel flag outlives the cancel by STATE_TTL and has to be dropped."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
        )
        channel_module.mark_cancelled(channel)
        assert channel_module.is_cancel_requested(channel.id) is True
        enable_continuous(channel)
        assert channel_module.is_cancel_requested(channel.id) is False


@pytest.mark.django_db
class TestHardening:
    def test_mark_finished_persists_error_durably(self, mailbox, user):
        """The failure reason must survive a Redis eviction (be in settings, not
        only in the ephemeral state) so a disabled poller can still explain why."""
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.IMAP.value
        )
        mark_finished(
            channel,
            status=enums.ImportStatus.FAILED.value,
            success=0,
            failure=0,
            error="authentication failed",
        )
        channel.refresh_from_db()
        assert channel.settings["import"]["error"] == "authentication failed"

    @pytest.mark.parametrize(
        "late_status",
        [enums.ImportStatus.COMPLETED.value, enums.ImportStatus.FAILED.value],
    )
    def test_mark_finished_never_downgrades_cancelled(self, mailbox, user, late_status):
        """A runner holding a stale in-memory channel (loaded at task start)
        must not overwrite a concurrent cancel's durable CANCELLED snapshot —
        that snapshot is the backstop when the Redis cancel flag is evicted."""
        channel = create_import_channel(
            recipient=mailbox, user=user, source_type=enums.ImportSource.IMAP.value
        )
        stale = models.Channel.objects.get(id=channel.id)
        channel_module.mark_cancelled(channel)
        # The evicted-flag worst case: only the durable status remains.
        channel_module.clear_state(channel.id)

        mark_finished(stale, status=late_status, success=5, failure=0, total=5)

        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )
        assert channel.is_active is False
        # The stale terminal write must not leak into Redis either.
        assert read_state(channel.id).get("status") is None

    def test_disable_continuous_demotes_poller(self, mailbox, user):
        """mode=oneshot demotion: polling stops but credentials + watermark stay
        so the import can be re-armed later."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.IMAP.value,
            imap_credentials={"username": "u", "password": "p"},
            mode=enums.ImportMode.CONTINUOUS.value,
        )
        channel_module.disable_continuous(channel)
        channel.refresh_from_db()
        assert channel.is_active is False
        assert channel.settings["import"]["mode"] == enums.ImportMode.ONESHOT.value
        assert channel.encrypted_settings["imap"]["username"] == "u"

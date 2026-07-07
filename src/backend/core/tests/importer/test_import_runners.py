"""End-to-end tests for the four unified import runners.

These call the per-format runners in the ``mbox``/``eml``/``pst``/``imap``
modules directly (the same functions ``run_import_task`` dispatches to),
exercising real delivery, dedup, resume watermarks and is_sender/draft handling
against real sample archives and a mocked IMAP server.
"""

# pylint: disable=redefined-outer-name, unused-argument

import socket
from unittest.mock import MagicMock, patch

from django.core.files.storage import storages

import pytest

from core import enums, factories, models
from core.services.importer.channel import create_import_channel, read_state
from core.services.importer.eml import run_eml
from core.services.importer.imap import run_imap
from core.services.importer.mbox import _mbox_plan, run_mbox
from core.services.importer.pst import run_pst
from core.services.importer.tasks import run_import_task
from core.services.importer.utils import TransientImportError, deliver

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def mailbox():
    return factories.MailboxFactory()


def _upload_to_s3(content, file_key):
    """Upload raw content to the message-imports S3 bucket (real object storage)."""
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    s3_client.put_object(Bucket=storage.bucket_name, Key=file_key, Body=content)
    return file_key, storage, s3_client


def _upload_file_to_s3(path, file_key):
    """Upload a sample resource file to the message-imports S3 bucket."""
    with open(path, "rb") as f:
        content = f.read()
    return _upload_to_s3(content, file_key)


def _upload_pst_to_s3(filename):
    """Upload a test PST file to the message-imports S3 bucket."""
    storage = storages["message-imports"]
    s3_client = storage.connection.meta.client
    with open(f"core/tests/resources/{filename}", "rb") as f:
        content = f.read()
    file_key = f"test-pst-{filename}"
    s3_client.put_object(
        Bucket=storage.bucket_name,
        Key=file_key,
        Body=content,
        ContentType="application/vnd.ms-outlook",
    )
    return file_key, storage, s3_client


def _eml_bytes(
    *, frm, to="someone@elsewhere.com", subject="Hello", message_id="<m1@x>"
):
    return (
        f"From: {frm}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        "Date: Mon, 26 May 2025 20:13:44 +0200\r\n"
        "\r\n"
        "body\r\n"
    ).encode()


# --- run_eml --------------------------------------------------------------


class TestRunEml:
    def test_delivers_one_message(self, mailbox, user):
        key = "runner-eml/message.eml"
        _, storage, s3_client = _upload_file_to_s3(
            "core/tests/resources/message.eml", key
        )
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.EML.value,
                file_key=key,
            )
            success, failure, total = run_eml(channel, {})
            assert (success, failure, total) == (1, 0, 1)
            message = models.Message.objects.get(channel=channel)
            # Import bodies go straight to the object-storage tier — a bulk
            # archive must not park its bytes in Postgres awaiting offload.
            assert (
                message.blob.storage_location
                == enums.BlobStorageLocationChoices.OBJECT_STORAGE
            )
            assert message.blob.raw_content is None
            assert message.blob.get_content()  # readable through the tier
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_from_equal_to_mailbox_marks_sender(self, mailbox, user):
        key = "runner-eml/sent.eml"
        _, storage, s3_client = _upload_to_s3(
            _eml_bytes(frm=str(mailbox), message_id="<sent@x>"), key
        )
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.EML.value,
                file_key=key,
            )
            success, failure, total = run_eml(channel, {})
            assert (success, failure, total) == (1, 0, 1)
            message = models.Message.objects.get(channel=channel)
            assert message.is_sender is True
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_undeliverable_eml_counts_failure(self, mailbox, user):
        """A file deliver() rejects (jmap parses almost anything, so the
        reliable trigger is empty content) must land in ``failure`` — not
        crash, not report success."""
        key = "runner-eml/empty.eml"
        _, storage, s3_client = _upload_to_s3(b"", key)
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.EML.value,
                file_key=key,
            )
            success, failure, total = run_eml(channel, {})
            assert (success, failure, total) == (0, 1, 1)
            assert not models.Message.objects.filter(channel=channel).exists()
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_oversized_eml_fails_run_with_explanatory_error(
        self, mailbox, user, settings
    ):
        """The size pre-check fails the whole run with a human-readable error
        (not a silent failure_count=1 on a COMPLETED run)."""
        settings.MAX_INCOMING_EMAIL_SIZE = 16
        key = "runner-eml/oversized.eml"
        _, storage, s3_client = _upload_to_s3(b"x" * 64, key)
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.EML.value,
                file_key=key,
            )
            result = run_import_task(str(channel.id))
            assert result["status"] == "FAILURE"
            channel.refresh_from_db()
            run = channel.settings["import"]
            assert run["status"] == enums.ImportStatus.FAILED.value
            assert "File too large" in run["error"]
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_resume_with_cursor_is_noop(self, mailbox, user):
        key = "runner-eml/noop.eml"
        _, storage, s3_client = _upload_to_s3(_eml_bytes(frm="sender@example.com"), key)
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.EML.value,
                file_key=key,
            )
            # A cursor >= 1 means the single eml was already delivered: the
            # runner returns the cached counts and delivers nothing new.
            success, failure, total = run_eml(
                channel, {"cursor": 1, "success": 1, "failure": 0}
            )
            assert (success, failure, total) == (1, 0, 1)
            assert models.Message.objects.filter(channel=channel).count() == 0
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_same_message_lands_in_two_mailboxes(self, user):
        """Cross-mailbox: dedup is per-mailbox, so the same message imported
        into two mailboxes yields one message in each."""
        mailbox_a = factories.MailboxFactory()
        mailbox_b = factories.MailboxFactory()
        key = "runner-eml/cross.eml"
        _, storage, s3_client = _upload_to_s3(
            _eml_bytes(frm="sender@example.com", message_id="<cross@x>"), key
        )
        try:
            for box in (mailbox_a, mailbox_b):
                channel = create_import_channel(
                    recipient=box,
                    user=user,
                    source_type=enums.ImportSource.EML.value,
                    file_key=key,
                )
                assert run_eml(channel, {})[0] == 1

            assert models.Message.objects.count() == 2
            assert (
                models.Message.objects.filter(
                    thread__accesses__mailbox=mailbox_a
                ).count()
                == 1
            )
            assert (
                models.Message.objects.filter(
                    thread__accesses__mailbox=mailbox_b
                ).count()
                == 1
            )
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)


class TestDeliverGuards:
    """deliver() is the only source of per-item ``failure`` counts: its two
    rejection branches must actually reject."""

    def test_oversized_message_is_rejected(self, mailbox, user, settings):
        settings.MAX_INCOMING_EMAIL_SIZE = 8
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.MBOX.value,
            file_key="k",
        )
        assert deliver(b"x" * 64, mailbox, channel) is False
        assert not models.Message.objects.filter(channel=channel).exists()

    def test_unparseable_message_is_rejected(self, mailbox, user):
        """jmap's parser is lenient (binary junk still yields a body-only
        message); empty bytes are the case it genuinely refuses."""
        channel = create_import_channel(
            recipient=mailbox,
            user=user,
            source_type=enums.ImportSource.MBOX.value,
            file_key="k",
        )
        assert deliver(b"", mailbox, channel) is False
        assert not models.Message.objects.filter(channel=channel).exists()


# --- run_mbox -------------------------------------------------------------


class TestRunMbox:
    def test_imports_every_message(self, mailbox, user):
        key = "runner-mbox/messages.mbox"
        _, storage, s3_client = _upload_file_to_s3(
            "core/tests/resources/messages.mbox", key
        )
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.MBOX.value,
                file_key=key,
            )
            success, failure, total = run_mbox(channel, {})
            assert total == 3
            assert success == 3
            assert failure == 0
            assert models.Message.objects.filter(channel=channel).count() == 3
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_resume_delivers_only_remaining_without_duplicates(self, mailbox, user):
        key = "runner-mbox/resume.mbox"
        _, storage, s3_client = _upload_file_to_s3(
            "core/tests/resources/messages.mbox", key
        )
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.MBOX.value,
                file_key=key,
            )
            # Resume as if the first two messages were already delivered: only
            # the last one (plan index 2) should be imported now.
            success, _failure, total = run_mbox(
                channel, {"cursor": 2, "success": 2, "failure": 0}
            )
            assert total == 3
            assert success == 3  # 2 carried over + 1 delivered now
            assert models.Message.objects.filter(channel=channel).count() == 1

            # A full re-run must not create duplicates (dedup by mime_id).
            run_mbox(channel, {})
            assert models.Message.objects.filter(channel=channel).count() == 3
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)

    def test_plan_ordering_is_stable(self, mailbox, user):
        key = "runner-mbox/plan.mbox"
        _, storage, s3_client = _upload_file_to_s3(
            "core/tests/resources/messages.mbox", key
        )
        try:
            plan = _mbox_plan(key)
            assert plan == _mbox_plan(key)
            # Each locator is a distinct, well-formed byte range (the plan is
            # date-ordered, so offsets need not be monotonic — but a duplicate
            # or inverted range would corrupt the resume cursor's message set).
            starts = [item["start"] for item in plan]
            assert len(set(starts)) == len(starts) == 3
            assert all(item["end"] >= item["start"] for item in plan)
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=key)


# --- run_pst --------------------------------------------------------------


class TestRunPst:
    def test_sample_pst_imports_message(self, mailbox, user):
        file_key, storage, s3_client = _upload_pst_to_s3("sample.pst")
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.PST.value,
                file_key=file_key,
            )
            success, failure, total = run_pst(channel, {})
            assert total == 1
            assert success == 1
            assert failure == 0

            message = models.Message.objects.get(channel=channel)
            assert message.sender.email == "from@domain.com"
            recipient_emails = sorted(r.contact.email for r in message.recipients.all())
            assert "to1@domain.com" in recipient_emails
            assert "cc1@domain.com" in recipient_emails
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_resume_from_cursor_delivers_only_remaining(self, mailbox, user):
        """PST resume: the deterministic plan + positional cursor mean a resumed
        run only delivers messages past the cursor."""
        file_key, storage, s3_client = _upload_pst_to_s3("Outlook.pst")
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.PST.value,
                file_key=file_key,
            )
            success, failure, total = run_pst(
                channel, {"cursor": 7, "success": 7, "failure": 0}
            )
            assert (success, failure, total) == (14, 0, 14)
            # Only the 7 messages past the cursor were actually delivered.
            assert models.Message.objects.filter(channel=channel).count() == 7
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_unreadable_pst_fails_run_with_dedicated_marker(self, mailbox, user):
        """A corrupt/garbage PST must fail the run with the ``PST_UNREADABLE``
        marker in the durable error — the import modal matches that marker to
        show its dedicated "retrying will not help" message."""
        file_key, storage, s3_client = _upload_to_s3(
            b"this is not a pst file" * 1024, "test-pst-garbage.pst"
        )
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.PST.value,
                file_key=file_key,
            )
            result = run_import_task(str(channel.id))
            assert result["status"] == "FAILURE"
            channel.refresh_from_db()
            run = channel.settings["import"]
            assert run["status"] == enums.ImportStatus.FAILED.value
            assert "PST_UNREADABLE" in run["error"]
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)

    def test_outlook_pst_marks_sent_folder_as_sender(self, mailbox, user):
        file_key, storage, s3_client = _upload_pst_to_s3("Outlook.pst")
        try:
            channel = create_import_channel(
                recipient=mailbox,
                user=user,
                source_type=enums.ImportSource.PST.value,
                file_key=file_key,
            )
            success, failure, total = run_pst(channel, {})
            # 8 Inbox + 6 Sent Items; Calendar/Contacts/Tasks skipped.
            assert total == 14
            assert success > 0
            assert failure == 0

            subjects = list(
                models.Message.objects.filter(channel=channel).values_list(
                    "subject", flat=True
                )
            )
            assert "Multiple attachments" in subjects

            # Exactly the 6 Sent-Items messages are marked as sent by the
            # mailbox — and the 8 Inbox ones are not.
            assert (success, failure) == (14, 0)
            assert (
                models.Message.objects.filter(channel=channel, is_sender=True).count()
                == 6
            )
            assert (
                models.Message.objects.filter(channel=channel, is_sender=False).count()
                == 8
            )
        finally:
            s3_client.delete_object(Bucket=storage.bucket_name, Key=file_key)


# --- run_imap -------------------------------------------------------------


def _imap_channel(mailbox, user, username="test@example.com"):
    return create_import_channel(
        recipient=mailbox,
        user=user,
        source_type=enums.ImportSource.IMAP.value,
        imap_credentials={
            "imap_server": "imap.example.com",
            "imap_port": 993,
            "username": username,
            "password": "password123",
            "use_ssl": True,
        },
    )


def _patch_imap(uid_map, folders=("INBOX",), uidvalidity=1):
    """Patch the IMAP helpers run_imap imports.

    ``uid_map`` maps folder -> {uid: (flags, raw_bytes)}.
    Returns a list of patchers the caller enters via ExitStack-like ``with``.
    """
    search = {folder: sorted(uid_map.get(folder, {})) for folder in folders}

    def _uid_search_all(_conn, folder, since_uid=0):
        return [u for u in search.get(folder, []) if u > since_uid]

    def _uid_fetch(_conn, uid):
        for per_folder in uid_map.values():
            if uid in per_folder:
                return per_folder[uid]
        raise AssertionError(f"unexpected uid {uid}")

    return [
        patch("core.services.importer.imap.IMAPConnectionManager"),
        patch(
            "core.services.importer.imap.get_selectable_folders",
            return_value=list(folders),
        ),
        patch(
            "core.services.importer.imap.create_folder_mapping",
            return_value={f: f for f in folders},
        ),
        patch(
            "core.services.importer.imap.get_folder_uidvalidity",
            return_value=uidvalidity,
        ),
        # run_imap re-selects each folder before its fetch pass (the planning
        # pass leaves the last-searched folder selected).
        patch(
            "core.services.importer.imap.select_imap_folder",
            return_value=True,
        ),
        patch(
            "core.services.importer.imap.uid_search_all",
            side_effect=_uid_search_all,
        ),
        patch(
            "core.services.importer.imap.uid_fetch_message",
            side_effect=_uid_fetch,
        ),
    ]


def _run_with_patches(patchers, fn):
    entered = [p.start() for p in patchers]
    try:
        return fn()
    finally:
        for p in patchers:
            p.stop()
        del entered


class TestRunImap:
    def test_delivers_messages_and_writes_watermark(self, mailbox, user):
        msg1 = _eml_bytes(frm="sender@example.com", subject="One", message_id="<i1@x>")
        msg2 = _eml_bytes(frm="sender@example.com", subject="Two", message_id="<i2@x>")
        uid_map = {"INBOX": {1: (["\\Seen"], msg1), 2: (["\\Seen"], msg2)}}
        channel = _imap_channel(mailbox, user)

        patchers = _patch_imap(uid_map)
        success, failure, total = _run_with_patches(
            patchers, lambda: run_imap(channel, {})
        )
        assert (success, failure, total) == (2, 0, 2)
        assert models.Message.objects.filter(channel=channel).count() == 2

        # Per-folder UID watermark is written to the live Redis state — and
        # ONLY there: folder names are remote-controlled, so the watermark must
        # never bloat the durable channel row (eviction just means a re-scan).
        state = read_state(channel.id)
        assert state["folders"]["INBOX"] == {"uidvalidity": 1, "last_uid": 2}
        channel.refresh_from_db()
        assert "folders" not in channel.settings["import"]

    def test_transient_fetch_failure_does_not_advance_watermark(self, mailbox, user):
        """A fetch that raises must leave the watermark below the failed UID (so
        a resume retries it — no silent loss) and surface a TransientImportError."""
        msg1 = _eml_bytes(frm="s@example.com", subject="One", message_id="<t1@x>")
        uid_map = {"INBOX": {1: (["\\Seen"], msg1), 2: (["\\Seen"], b"unused")}}

        def _flaky_fetch(_conn, uid):
            if uid == 2:
                raise TimeoutError("fetch blip")
            return uid_map["INBOX"][uid]

        channel = _imap_channel(mailbox, user)
        patchers = _patch_imap(uid_map)
        # Swap the fetch patcher for our flaky one (it's the last in the list).
        patchers[-1] = patch(
            "core.services.importer.imap.uid_fetch_message",
            side_effect=_flaky_fetch,
        )
        with pytest.raises(TransientImportError):
            _run_with_patches(patchers, lambda: run_imap(channel, {}))

        # uid 1 was delivered; the watermark stopped at 1, NOT 2.
        assert models.Message.objects.filter(channel=channel).count() == 1
        state = read_state(channel.id)
        assert state["folders"]["INBOX"]["last_uid"] == 1

    def test_missing_uidvalidity_imports_with_full_rescan(self, mailbox, user):
        """A folder whose STATUS fails (no UIDVALIDITY) is still imported —
        with a full re-scan each run, deduped instead of silently skipped."""
        msg1 = _eml_bytes(frm="s@example.com", subject="One", message_id="<u1@x>")
        msg2 = _eml_bytes(frm="s@example.com", subject="Two", message_id="<u2@x>")
        uid_map = {"INBOX": {1: (["\\Seen"], msg1), 2: ([], msg2)}}

        channel = _imap_channel(mailbox, user)
        success, failure, total = _run_with_patches(
            _patch_imap(uid_map, uidvalidity=None), lambda: run_imap(channel, {})
        )
        assert (success, failure, total) == (2, 0, 2)
        assert models.Message.objects.filter(channel=channel).count() == 2

        # Second run: the watermark can't be trusted without UIDVALIDITY, so
        # everything is re-scanned — and deduped down to zero new messages.
        state = read_state(channel.id)
        success, failure, total = _run_with_patches(
            _patch_imap(uid_map, uidvalidity=None),
            lambda: run_imap(channel, state),
        )
        assert models.Message.objects.filter(channel=channel).count() == 2

    def test_second_run_with_watermark_adds_nothing(self, mailbox, user):
        msg1 = _eml_bytes(frm="sender@example.com", subject="One", message_id="<j1@x>")
        msg2 = _eml_bytes(frm="sender@example.com", subject="Two", message_id="<j2@x>")
        uid_map = {"INBOX": {1: (["\\Seen"], msg1), 2: (["\\Seen"], msg2)}}
        channel = _imap_channel(mailbox, user)

        _run_with_patches(_patch_imap(uid_map), lambda: run_imap(channel, {}))
        assert models.Message.objects.filter(channel=channel).count() == 2

        # Resume with the prior watermark: uid_search returns the same UIDs but
        # all are <= last_uid, so nothing new is fetched and total is unchanged.
        prior = {
            "folders": {"INBOX": {"uidvalidity": 1, "last_uid": 2}},
            "success": 2,
            "failure": 0,
            "total": 2,
        }
        success, failure, total = _run_with_patches(
            _patch_imap(uid_map), lambda: run_imap(channel, prior)
        )
        assert (success, failure, total) == (2, 0, 2)
        assert models.Message.objects.filter(channel=channel).count() == 2

    def test_duplicate_recipients_are_deduplicated(self, mailbox, user):
        raw = (
            "From: sender@example.com\r\n"
            "To: recipient@example.com, recipient@example.com\r\n"
            "Cc: cc@example.com, cc@example.com\r\n"
            "Subject: Dupes\r\n"
            "Message-ID: <dupes@x>\r\n"
            "Date: Mon, 26 May 2025 20:13:44 +0200\r\n"
            "\r\n"
            "body\r\n"
        ).encode()
        uid_map = {"INBOX": {1: (["\\Seen"], raw)}}
        channel = _imap_channel(mailbox, user)

        success, _failure, _total = _run_with_patches(
            _patch_imap(uid_map), lambda: run_imap(channel, {})
        )
        assert success == 1
        message = models.Message.objects.get(channel=channel)
        emails = [r.contact.email for r in message.recipients.all()]
        assert len(emails) == len(set(emails))
        assert (
            message.recipients.filter(type=enums.MessageRecipientTypeChoices.TO).count()
            == 1
        )
        assert (
            message.recipients.filter(type=enums.MessageRecipientTypeChoices.CC).count()
            == 1
        )

    def test_draft_flag_leaves_recipients_unsent(self, mailbox, user):
        raw = (
            "From: sender@example.com\r\n"
            "To: r1@example.com, r2@example.com\r\n"
            "Cc: cc@example.com\r\n"
            "Subject: Draft Message\r\n"
            "Message-ID: <draft@x>\r\n"
            "Date: Mon, 26 May 2025 20:13:44 +0200\r\n"
            "\r\n"
            "draft body\r\n"
        ).encode()
        uid_map = {"INBOX": {1: (["\\Draft"], raw)}}
        channel = _imap_channel(mailbox, user)

        _run_with_patches(_patch_imap(uid_map), lambda: run_imap(channel, {}))
        message = models.Message.objects.get(channel=channel)
        assert message.is_draft is True
        recipients = message.recipients.all()
        assert recipients.count() == 3
        for recipient in recipients:
            assert recipient.delivery_status is None

    def test_non_draft_recipients_are_marked_sent(self, mailbox, user):
        raw = _eml_bytes(
            frm="sender@example.com",
            to="recipient@example.com",
            subject="Regular",
            message_id="<regular@x>",
        )
        uid_map = {"INBOX": {1: (["\\Seen"], raw)}}
        channel = _imap_channel(mailbox, user)

        _run_with_patches(_patch_imap(uid_map), lambda: run_imap(channel, {}))
        message = models.Message.objects.get(channel=channel)
        assert message.is_draft is False
        recipient = message.recipients.get()
        assert (
            recipient.delivery_status
            == enums.MessageDeliveryStatusChoices.SENT_EXTERNAL
        )

    def test_reselect_failure_is_transient_not_silent(self, mailbox, user):
        """A folder that fails to re-select before its fetch pass must raise
        (transient) rather than be skipped: skipping would let a oneshot run
        end COMPLETED with the folder's mail silently missing forever."""
        raw = _eml_bytes(frm="sender@example.com", subject="X", message_id="<rs@x>")
        uid_map = {"INBOX": {1: ([], raw)}}
        channel = _imap_channel(mailbox, user)

        def run():
            with patch(
                "core.services.importer.imap.select_imap_folder",
                return_value=False,
            ):
                with pytest.raises(TransientImportError):
                    run_imap(channel, {})

        _run_with_patches(_patch_imap(uid_map), run)
        # Nothing delivered, watermark untouched: the retry loses nothing.
        assert not models.Message.objects.filter(channel=channel).exists()

    def test_connection_error_is_transient(self, mailbox, user):
        """A connect-time network failure (DNS, socket timeout) is exactly as
        transient as a mid-run fetch failure: it must map to
        TransientImportError (retry budget) rather than terminally FAILING —
        and permanently disabling — a continuous poller over one blip."""
        channel = _imap_channel(mailbox, user)
        with patch("core.services.importer.imap.IMAPConnectionManager") as mgr:
            mgr.return_value.__enter__.side_effect = socket.gaierror("dns down")
            with pytest.raises(TransientImportError):
                run_imap(channel, {})


# --- _collect_pst_plan cancellation -----------------------------------------


def test_collect_pst_plan_propagates_cancel():
    """The cancel raised by beat()/on_progress during the PST pre-scan must
    unwind the run — not be swallowed by the corrupt-folder except blocks,
    which would keep the worker scanning (and holding the run lock) for the
    rest of the archive."""
    from core.services.importer import pst as pst_module
    from core.services.importer.channel import ImportCancelled

    folder = MagicMock()
    folder.number_of_sub_messages = 3
    folder.number_of_sub_folders = 0
    folder.name = "Inbox"
    root = MagicMock()
    root.number_of_sub_folders = 1
    root.get_sub_folder.return_value = folder

    def cancelling_beat():
        raise ImportCancelled()

    with (
        patch.object(pst_module, "_find_ipm_subtree", return_value=root),
        patch.object(pst_module, "build_well_known_folder_map", return_value={}),
        patch.object(pst_module, "_is_email_folder", return_value=True),
        patch.object(
            pst_module, "_get_folder_type", return_value=pst_module.FOLDER_TYPE_NORMAL
        ),
    ):
        with pytest.raises(ImportCancelled):
            pst_module._collect_pst_plan(MagicMock(), {}, on_progress=cancelling_beat)

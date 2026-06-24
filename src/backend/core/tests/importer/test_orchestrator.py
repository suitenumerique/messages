"""Tests for the resumable import orchestrator (axis 2).

These exercise the orchestration state machine (batch completion + finalize,
start fan-out, reaper) WITHOUT delivering messages: ``_process_batch`` /
``_build_message_plan`` / the batch task's ``.delay`` are mocked, so no
OpenSearch reindex happens and the tests stay fast and infra-light.
"""

# pylint: disable=redefined-outer-name, unused-argument, protected-access

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.utils import timezone

import pytest

from core import enums, factories
from core.services.importer.channel import (
    create_import_channel,
    record_batch_completion,
    update_import_state,
)
from core.services.importer.orchestrator import (
    ImapUidValidityChanged,
    _build_message_plan,
    _chunk,
    _index_imap,
    _process_batch,
    _process_imap_batch,
    process_import_batch_task,
    reap_stalled_imports_task,
    start_import_task,
)


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def mailbox():
    return factories.MailboxFactory()


def _import(mailbox, user, source=enums.ImportSource.MBOX):
    return create_import_channel(
        recipient=mailbox, user=user, source_type=source.value, file_key="archive"
    )


class TestPlanHelpers:
    def test_chunk(self):
        assert _chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
        assert _chunk([], 2) == []

    def test_build_plan_eml(self):
        assert _build_message_plan(enums.ImportSource.EML.value, "k") == [{"eml": True}]

    def test_build_plan_unsupported_raises(self):
        with pytest.raises(ValueError, match="does not support"):
            _build_message_plan("carrier-pigeon", "k")

    @patch("core.services.importer.orchestrator._index_pst")
    def test_build_plan_pst_delegates_to_index(self, mock_index_pst):
        mock_index_pst.return_value = [{"folder_id": 7, "msg_index": 0}]
        plan = _build_message_plan(enums.ImportSource.PST.value, "archive.pst")
        assert plan == [{"folder_id": 7, "msg_index": 0}]
        mock_index_pst.assert_called_once_with("archive.pst")


@pytest.mark.django_db
class TestRecordBatchCompletion:
    def test_increments_without_finalizing(self, mailbox, user):
        channel = _import(mailbox, user)
        update_import_state(
            channel.id, status=enums.ImportStatus.RUNNING.value, total_batches=2
        )

        finalized = record_batch_completion(
            channel.id, batch_number=0, success_count=3, failure_count=1
        )
        assert finalized is False
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert run["completed_batches"] == [0]
        assert run["success_count"] == 3
        assert run["failure_count"] == 1
        assert run["status"] == enums.ImportStatus.RUNNING.value

    def test_last_batch_finalizes(self, mailbox, user):
        channel = _import(mailbox, user)
        update_import_state(
            channel.id, status=enums.ImportStatus.RUNNING.value, total_batches=2
        )
        record_batch_completion(
            channel.id, batch_number=0, success_count=3, failure_count=0
        )
        finalized = record_batch_completion(
            channel.id, batch_number=1, success_count=2, failure_count=1
        )
        assert finalized is True
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert set(run["completed_batches"]) == {0, 1}
        assert run["status"] == enums.ImportStatus.COMPLETED.value
        assert run["success_count"] == 5
        assert run["failure_count"] == 1
        assert run["finished_at"] is not None

    def test_cancelled_is_not_finalized(self, mailbox, user):
        channel = _import(mailbox, user)
        update_import_state(
            channel.id, status=enums.ImportStatus.CANCELLED.value, total_batches=1
        )
        finalized = record_batch_completion(
            channel.id, batch_number=0, success_count=1, failure_count=0
        )
        assert finalized is False
        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.CANCELLED.value
        )

    def test_replaying_a_batch_does_not_double_count_number(self, mailbox, user):
        channel = _import(mailbox, user)
        update_import_state(
            channel.id, status=enums.ImportStatus.RUNNING.value, total_batches=3
        )
        record_batch_completion(
            channel.id, batch_number=0, success_count=1, failure_count=0
        )
        record_batch_completion(
            channel.id, batch_number=0, success_count=1, failure_count=0
        )
        channel.refresh_from_db()
        # The number appears once; the run is not falsely finalized.
        assert channel.settings["import"]["completed_batches"] == [0]
        assert channel.settings["import"]["status"] == enums.ImportStatus.RUNNING.value


@pytest.mark.django_db
class TestStartImportTask:
    @patch("core.services.importer.orchestrator.process_import_batch_task")
    @patch("core.services.importer.orchestrator._build_message_plan")
    @override_settings(MESSAGES_IMPORT_BATCH_SIZE=500)
    def test_indexes_and_fans_out(self, mock_plan, mock_batch, mailbox, user):
        mock_plan.return_value = [{"start": i, "end": i + 1} for i in range(1200)]
        channel = _import(mailbox, user)

        result = start_import_task(str(channel.id))

        assert result["total_messages"] == 1200
        assert result["total_batches"] == 3  # 500 + 500 + 200
        assert mock_batch.delay.call_count == 3
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert run["status"] == enums.ImportStatus.RUNNING.value
        assert run["total_batches"] == 3
        assert run["completed_batches"] == []

    @patch("core.services.importer.orchestrator.process_import_batch_task")
    @patch("core.services.importer.orchestrator._build_message_plan")
    def test_empty_plan_completes_immediately(
        self, mock_plan, mock_batch, mailbox, user
    ):
        mock_plan.return_value = []
        channel = _import(mailbox, user)

        start_import_task(str(channel.id))

        assert mock_batch.delay.call_count == 0
        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )

    @patch("core.services.importer.orchestrator._build_message_plan")
    def test_index_failure_marks_failed(self, mock_plan, mailbox, user):
        mock_plan.side_effect = RuntimeError("corrupt archive")
        channel = _import(mailbox, user)

        result = start_import_task(str(channel.id))

        assert result["status"] == "FAILURE"
        channel.refresh_from_db()
        assert channel.settings["import"]["status"] == enums.ImportStatus.FAILED.value

    @patch("core.services.importer.orchestrator.process_import_batch_task")
    @patch("core.services.importer.orchestrator._build_message_plan")
    @override_settings(MESSAGES_IMPORT_BATCH_SIZE=500)
    def test_rerun_resumes_without_resetting_progress(
        self, mock_plan, mock_batch, mailbox, user
    ):
        mock_plan.return_value = [{"start": i, "end": i + 1} for i in range(1000)]
        channel = _import(mailbox, user)
        update_import_state(
            channel.id,
            status=enums.ImportStatus.RUNNING.value,
            total_batches=2,
            batch_size=500,
            completed_batches=[0],
        )

        result = start_import_task(str(channel.id))

        assert result["status"] == "RESUMED"
        # Only the missing batch is re-dispatched; progress is preserved.
        assert mock_batch.delay.call_count == 1
        assert mock_batch.delay.call_args[0][1] == 1
        channel.refresh_from_db()
        assert channel.settings["import"]["completed_batches"] == [0]


@pytest.mark.django_db
class TestProcessBatchTask:
    @patch("core.services.importer.orchestrator._process_batch")
    def test_processes_and_records(self, mock_process, mailbox, user):
        mock_process.return_value = (3, 1)
        channel = _import(mailbox, user)
        update_import_state(
            channel.id, status=enums.ImportStatus.RUNNING.value, total_batches=2
        )

        result = process_import_batch_task(str(channel.id), 0, [{"start": 0, "end": 9}])

        assert result["success"] == 3
        assert result["failure"] == 1
        channel.refresh_from_db()
        run = channel.settings["import"]
        assert run["completed_batches"] == [0]
        assert run["success_count"] == 3

    @patch("core.services.importer.orchestrator._process_batch")
    def test_skips_when_cancelled(self, mock_process, mailbox, user):
        channel = _import(mailbox, user)
        update_import_state(
            channel.id, status=enums.ImportStatus.CANCELLED.value, total_batches=1
        )

        result = process_import_batch_task(str(channel.id), 0, [{"start": 0, "end": 9}])

        assert result["status"] == "CANCELLED"
        mock_process.assert_not_called()

    @patch("core.services.importer.orchestrator._process_batch")
    def test_skips_already_completed_batch(self, mock_process, mailbox, user):
        channel = _import(mailbox, user)
        update_import_state(
            channel.id,
            status=enums.ImportStatus.RUNNING.value,
            total_batches=2,
            completed_batches=[0],
        )

        result = process_import_batch_task(str(channel.id), 0, [{"start": 0, "end": 9}])

        assert result["status"] == "ALREADY_DONE"
        mock_process.assert_not_called()


@pytest.mark.django_db
class TestReaper:
    @patch("core.services.importer.orchestrator.process_import_batch_task")
    @patch("core.services.importer.orchestrator._build_message_plan")
    @override_settings(
        MESSAGES_IMPORT_STALL_TIMEOUT=900, MESSAGES_IMPORT_BATCH_SIZE=500
    )
    def test_redispatches_only_missing_batches(
        self, mock_plan, mock_batch, mailbox, user
    ):
        mock_plan.return_value = [{"start": i, "end": i + 1} for i in range(1000)]
        channel = _import(mailbox, user)
        stale = (timezone.now() - timedelta(seconds=5000)).isoformat()
        update_import_state(
            channel.id,
            status=enums.ImportStatus.RUNNING.value,
            total_batches=2,
            batch_size=500,
            completed_batches=[0],
            heartbeat=stale,
        )

        result = reap_stalled_imports_task()

        assert result["redispatched"] == 1
        mock_batch.delay.assert_called_once()
        # Only the missing batch (number 1) is re-dispatched.
        assert mock_batch.delay.call_args[0][1] == 1

    @patch("core.services.importer.orchestrator.process_import_batch_task")
    @patch("core.services.importer.orchestrator._build_message_plan")
    @override_settings(MESSAGES_IMPORT_STALL_TIMEOUT=900)
    def test_skips_fresh_imports(self, mock_plan, mock_batch, mailbox, user):
        mock_plan.return_value = [{"start": 0, "end": 1}]
        channel = _import(mailbox, user)
        update_import_state(
            channel.id,
            status=enums.ImportStatus.RUNNING.value,
            total_batches=1,
            completed_batches=[],
            heartbeat=timezone.now().isoformat(),  # fresh
        )

        result = reap_stalled_imports_task()

        assert result["redispatched"] == 0
        mock_batch.delay.assert_not_called()

    @patch("core.services.importer.orchestrator.process_import_batch_task")
    @patch("core.services.importer.orchestrator._build_message_plan")
    @override_settings(
        MESSAGES_IMPORT_STALL_TIMEOUT=900, MESSAGES_IMPORT_BATCH_SIZE=500
    )
    def test_finalizes_stalled_run_with_all_batches_done(
        self, mock_plan, mock_batch, mailbox, user
    ):
        mock_plan.return_value = [{"start": i, "end": i + 1} for i in range(1000)]
        channel = _import(mailbox, user)
        stale = (timezone.now() - timedelta(seconds=5000)).isoformat()
        update_import_state(
            channel.id,
            status=enums.ImportStatus.RUNNING.value,
            total_batches=2,
            batch_size=500,
            completed_batches=[0, 1],
            success_count=900,
            failure_count=100,
            heartbeat=stale,
        )

        result = reap_stalled_imports_task()

        assert result["redispatched"] == 0
        mock_batch.delay.assert_not_called()
        channel.refresh_from_db()
        assert (
            channel.settings["import"]["status"] == enums.ImportStatus.COMPLETED.value
        )


_IMAP_CREDS = {
    "imap_server": "imap.example.com",
    "imap_port": 993,
    "username": "u@example.com",
    "password": "secret",
    "use_ssl": True,
}


def _imap_import(mailbox, user, prior_folders=None):
    """Create an IMAP import channel with stored credentials."""
    channel = create_import_channel(
        recipient=mailbox,
        user=user,
        source_type=enums.ImportSource.IMAP.value,
        imap_credentials=dict(_IMAP_CREDS),
    )
    if prior_folders is not None:
        update_import_state(channel.id, imap={"folders": prior_folders})
        channel.refresh_from_db()
    return channel


@pytest.mark.django_db
class TestImapIndex:
    """``_index_imap`` enumerates UIDs and guards UIDVALIDITY (imaplib mocked)."""

    @patch("core.services.importer.orchestrator.uid_search_all")
    @patch("core.services.importer.orchestrator.get_folder_uidvalidity")
    @patch("core.services.importer.orchestrator.create_folder_mapping")
    @patch("core.services.importer.orchestrator.get_selectable_folders")
    @patch("core.services.importer.orchestrator.IMAPConnectionManager")
    def test_enumerates_uids_per_folder(
        self,
        mock_mgr,
        mock_folders,
        mock_mapping,
        mock_uidv,
        mock_search,
        mailbox,
        user,
    ):
        mock_mgr.return_value.__enter__.return_value = MagicMock()
        mock_folders.return_value = ["INBOX", "Sent"]
        mock_mapping.return_value = {"INBOX": "INBOX", "Sent": "Sent"}
        mock_uidv.side_effect = lambda _conn, folder: {"INBOX": 10, "Sent": 20}[folder]
        mock_search.side_effect = lambda _conn, folder: {
            "INBOX": [1, 2, 3],
            "Sent": [5],
        }[folder]
        channel = _imap_import(mailbox, user)

        locators = _index_imap(channel)

        assert len(locators) == 4
        inbox = [loc for loc in locators if loc["folder"] == "INBOX"]
        assert [loc["uid"] for loc in inbox] == [1, 2, 3]
        assert all(loc["uidvalidity"] == 10 for loc in inbox)
        # Credentials never leak into the (broker-persisted) locators.
        assert all("password" not in loc for loc in locators)
        # Folder metadata is persisted so a re-index can detect drift.
        channel.refresh_from_db()
        meta = {m["name"]: m for m in channel.settings["import"]["imap"]["folders"]}
        assert meta["INBOX"]["uid_count"] == 3
        assert meta["Sent"]["uidvalidity"] == 20

    @patch("core.services.importer.orchestrator.uid_search_all")
    @patch("core.services.importer.orchestrator.get_folder_uidvalidity")
    @patch("core.services.importer.orchestrator.create_folder_mapping")
    @patch("core.services.importer.orchestrator.get_selectable_folders")
    @patch("core.services.importer.orchestrator.IMAPConnectionManager")
    def test_raises_when_uidvalidity_changed(
        self,
        mock_mgr,
        mock_folders,
        mock_mapping,
        mock_uidv,
        mock_search,
        mailbox,
        user,
    ):
        mock_mgr.return_value.__enter__.return_value = MagicMock()
        mock_folders.return_value = ["INBOX"]
        mock_mapping.return_value = {"INBOX": "INBOX"}
        mock_uidv.return_value = 99  # live differs from the stored 10
        mock_search.return_value = [1]
        channel = _imap_import(
            mailbox,
            user,
            prior_folders=[{"name": "INBOX", "uidvalidity": 10, "uid_count": 1}],
        )

        with pytest.raises(ImapUidValidityChanged):
            _index_imap(channel)


@pytest.mark.django_db
class TestImapBatch:
    """``_process_imap_batch`` reconnects, re-verifies UIDVALIDITY, delivers."""

    @patch("core.services.importer.orchestrator.deliver_inbound_message")
    @patch("core.services.importer.orchestrator.uid_fetch_message")
    @patch("core.services.importer.orchestrator.select_imap_folder")
    @patch("core.services.importer.orchestrator.get_folder_uidvalidity")
    @patch("core.services.importer.orchestrator.IMAPConnectionManager")
    def test_fetches_and_delivers(
        self,
        mock_mgr,
        mock_uidv,
        mock_select,
        mock_fetch,
        mock_deliver,
        mailbox,
        user,
    ):
        mock_mgr.return_value.__enter__.return_value = MagicMock()
        mock_uidv.return_value = 10
        mock_select.return_value = True
        mock_fetch.return_value = (
            ["\\Seen"],
            b"From: a@example.com\r\nSubject: hi\r\n\r\nbody",
        )
        mock_deliver.return_value = MagicMock()  # truthy => delivered
        channel = _imap_import(mailbox, user)
        locators = [
            {"folder": "INBOX", "display_name": "INBOX", "uidvalidity": 10, "uid": 1},
            {"folder": "INBOX", "display_name": "INBOX", "uidvalidity": 10, "uid": 2},
        ]

        success, failure = _process_imap_batch(locators, mailbox, channel)

        assert (success, failure) == (2, 0)
        assert mock_deliver.call_count == 2
        # Parallel-safe import delivery must opt into the per-mailbox lock.
        assert mock_deliver.call_args.kwargs["force_lock"] is True
        assert mock_deliver.call_args.kwargs["is_import"] is True

    @patch("core.services.importer.orchestrator.deliver_inbound_message")
    @patch("core.services.importer.orchestrator.uid_fetch_message")
    @patch("core.services.importer.orchestrator.select_imap_folder")
    @patch("core.services.importer.orchestrator.get_folder_uidvalidity")
    @patch("core.services.importer.orchestrator.IMAPConnectionManager")
    def test_skips_folder_on_uidvalidity_change(
        self,
        mock_mgr,
        mock_uidv,
        mock_select,
        mock_fetch,
        mock_deliver,
        mailbox,
        user,
    ):
        mock_mgr.return_value.__enter__.return_value = MagicMock()
        mock_uidv.return_value = 999  # differs from the locator's 10
        channel = _imap_import(mailbox, user)
        locators = [
            {"folder": "INBOX", "display_name": "INBOX", "uidvalidity": 10, "uid": 1},
        ]

        success, failure = _process_imap_batch(locators, mailbox, channel)

        assert (success, failure) == (0, 1)
        mock_fetch.assert_not_called()
        mock_deliver.assert_not_called()

    @patch("core.services.importer.orchestrator._process_imap_batch")
    def test_process_batch_routes_imap(self, mock_imap_batch, mailbox, user):
        mock_imap_batch.return_value = (1, 0)
        channel = _imap_import(mailbox, user)
        locators = [
            {"folder": "INBOX", "display_name": "INBOX", "uidvalidity": 10, "uid": 1},
        ]

        result = _process_batch(
            enums.ImportSource.IMAP.value, None, locators, mailbox, channel
        )

        assert result == (1, 0)
        mock_imap_batch.assert_called_once_with(locators, mailbox, channel)


@pytest.mark.django_db
class TestPstDispatch:
    """``_process_batch`` routes PST locators to the PST batch handler."""

    @patch("core.services.importer.orchestrator._process_pst_batch")
    def test_process_batch_routes_pst(self, mock_pst_batch, mailbox, user):
        mock_pst_batch.return_value = (2, 1)
        channel = _import(mailbox, user, source=enums.ImportSource.PST)
        locators = [{"folder_id": 7, "msg_index": 0}]

        result = _process_batch(
            enums.ImportSource.PST.value, "archive.pst", locators, mailbox, channel
        )

        assert result == (2, 1)
        mock_pst_batch.assert_called_once_with(
            "archive.pst", locators, mailbox, channel
        )

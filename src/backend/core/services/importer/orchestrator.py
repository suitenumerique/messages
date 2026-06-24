"""Resumable, batch-based import orchestrator.

Splits a file import into fixed-size batches so a crash loses only the
in-flight batch instead of the whole run, and a periodic reaper re-dispatches
whatever is left. There is no chord: each batch records its own completion
under a row lock and the last one finalizes the run, which survives a worker
crash that a chord callback would not.

Handles all four sources behind ``MESSAGES_IMPORT_USE_ORCHESTRATOR``: ``mbox``
(byte-range locators), ``eml`` (single message), ``pst`` (``(folder_id,
msg_index)`` locators re-resolved against a freshly opened archive) and
``imap`` (``(folder, uid)`` locators guarded by UIDVALIDITY, with credentials
read fresh from the channel each batch — never passed as task args).

Re-running a batch is safe: ``deliver_inbound_message`` dedups by mime_id, so
the reaper (and Celery retries) never create duplicates. Parallel batches of
one import opt into the per-mailbox lock via ``force_lock=True``. IMAP resume
relies on stable UIDs: a folder whose UIDVALIDITY changed fails the run rather
than importing the wrong messages.
"""

# pylint: disable=broad-exception-caught
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.conf import settings
from django.core.files.storage import storages
from django.utils import timezone

import pypff
from celery.utils.log import get_task_logger
from jmap_email import first_address_email, parse_email

from core import enums, models
from core.mda.inbound import deliver_inbound_message
from core.utils import ThreadReindexDeferrer, ThreadStatsUpdateDeferrer

from messages.celery_app import app as celery_app

from .channel import (
    get_import_channel,
    mark_finished,
    record_batch_completion,
    scrub_import_credentials,
    update_import_state,
)
from .imap import (
    IMAPConnectionManager,
    create_folder_mapping,
    get_folder_uidvalidity,
    get_selectable_folders,
    select_imap_folder,
    uid_fetch_message,
    uid_search_all,
)
from .mbox_tasks import index_mbox_messages
from .pst import (
    assert_pst_readable,
    build_pst_folder_map,
    build_special_folder_map,
    collect_pst_message_index,
    compute_pst_labels_flags,
    get_store_owner_email,
    reconstruct_eml_for_locator,
)
from .s3_seekable import BUFFER_CENTERED, BUFFER_NONE, S3SeekableReader

logger = get_task_logger(__name__)

# Source types the orchestrator can split into resumable batches.
SUPPORTED_SOURCES = frozenset(
    {
        enums.ImportSource.MBOX.value,
        enums.ImportSource.EML.value,
        enums.ImportSource.PST.value,
        enums.ImportSource.IMAP.value,
    }
)


class ImapUidValidityChanged(RuntimeError):
    """An IMAP folder's UIDVALIDITY changed between indexing and (re)dispatch.

    The previously-collected UIDs no longer address the same messages, so the
    run cannot be resumed safely and is failed with a clear cause instead of
    importing the wrong messages.
    """


def _imports_storage():
    """The message-imports bucket + its low-level S3 client."""
    storage = storages["message-imports"]
    return storage, storage.connection.meta.client


def _index_mbox(file_key: str) -> list[dict[str, int]]:
    """Return byte-range locators for every message, sorted oldest-first."""
    storage, s3_client = _imports_storage()
    with S3SeekableReader(
        s3_client,
        storage.bucket_name,
        file_key,
        buffer_strategy=BUFFER_CENTERED,
    ) as reader:
        indices = index_mbox_messages(reader)

    # Oldest-first; messages without a parseable date sort last. Naive dates are
    # treated as UTC (parsedate returns naive for "-0000"). Deterministic, so the
    # reaper rebuilds identical batches.
    max_date = datetime.max.replace(tzinfo=dt_timezone.utc)
    indices.sort(
        key=lambda m: (
            m.date is None,
            m.date.replace(tzinfo=dt_timezone.utc)
            if m.date and m.date.tzinfo is None
            else (m.date or max_date),
        )
    )
    return [{"start": i.start_byte, "end": i.end_byte} for i in indices]


def _build_message_plan(
    source_type: str, file_key: str, channel: models.Channel | None = None
) -> list[dict[str, Any]]:
    """Ordered, deterministic per-message locator list for ``source_type``.

    ``channel`` is only consulted for IMAP (it carries the stored credentials
    needed to enumerate the server); file-based sources ignore it.
    """
    if source_type == enums.ImportSource.MBOX.value:
        return _index_mbox(file_key)
    if source_type == enums.ImportSource.EML.value:
        return [{"eml": True}]
    if source_type == enums.ImportSource.PST.value:
        return _index_pst(file_key)
    if source_type == enums.ImportSource.IMAP.value:
        return _index_imap(channel)
    raise ValueError(f"Orchestrator does not support source_type={source_type!r}")


def _deliver_one(
    raw_bytes: bytes, recipient: models.Mailbox, channel: models.Channel
) -> bool:
    """Parse + deliver one message under the import channel. Returns success."""
    if len(raw_bytes) > settings.MAX_INCOMING_EMAIL_SIZE:
        logger.warning("import: skipping oversized message (%d bytes)", len(raw_bytes))
        return False
    parsed_email = parse_email(raw_bytes)
    if parsed_email is None:
        logger.warning(
            "import: skipping unparseable message (%d bytes)", len(raw_bytes)
        )
        return False

    recipient_email = str(recipient)
    sender_email = first_address_email(parsed_email.get("from"))
    # Treat as a sent message when From matches the destination mailbox (same
    # heuristic as the monolithic tasks), so own sent mail doesn't land in inbox.
    is_import_sender = (
        bool(sender_email) and sender_email.lower() == recipient_email.lower()
    )
    return bool(
        deliver_inbound_message(
            recipient_email,
            parsed_email,
            raw_bytes,
            is_import=True,
            is_import_sender=is_import_sender,
            channel=channel,
            force_lock=True,
        )
    )


def _process_mbox_batch(
    file_key: str,
    locators: list[dict[str, int]],
    recipient: models.Mailbox,
    channel: models.Channel,
) -> tuple[int, int]:
    """Read this batch's byte ranges from S3 and deliver each message."""
    storage, s3_client = _imports_storage()
    success = failure = 0
    with S3SeekableReader(
        s3_client,
        storage.bucket_name,
        file_key,
        buffer_strategy=BUFFER_CENTERED,
    ) as reader:
        for loc in locators:
            try:
                reader.seek(loc["start"])
                raw = reader.read(loc["end"] - loc["start"] + 1)
                if _deliver_one(raw, recipient, channel):
                    success += 1
                else:
                    failure += 1
            except Exception:
                logger.exception(
                    "import: error processing mbox message in %s", file_key
                )
                failure += 1
    return success, failure


def _process_eml_batch(
    file_key: str,
    locators: list[dict[str, Any]],
    recipient: models.Mailbox,
    channel: models.Channel,
) -> tuple[int, int]:
    """Read the single EML (size-limited) from S3 and deliver it."""
    storage, s3_client = _imports_storage()
    limit = settings.MAX_INCOMING_EMAIL_SIZE
    resp = s3_client.get_object(
        Bucket=storage.bucket_name, Key=file_key, Range=f"bytes=0-{limit}"
    )
    raw = resp["Body"].read()
    if len(raw) > limit:
        logger.warning("import: eml file too large (> %d bytes)", limit)
        return 0, 1
    return (1, 0) if _deliver_one(raw, recipient, channel) else (0, 1)


# --- PST -------------------------------------------------------------------


def _open_pst(file_key: str):
    """Open a PST from S3 behind a block-aligned LRU cache sized for pypff's
    random-access B-tree traversal (64 KB x 2048 = 128 MB cap).

    Returns ``(reader, pst)``; the caller must close both.
    """
    storage, s3_client = _imports_storage()
    reader = S3SeekableReader(
        s3_client,
        storage.bucket_name,
        file_key,
        buffer_strategy=BUFFER_NONE,
        buffer_size=64 * 1024,
        buffer_count=2048,
    )
    try:
        pst = pypff.file()
        pst.open_file_object(reader)
    except Exception:
        # Don't leak the reader's buffers if libpff rejects the archive.
        reader.close()
        raise
    return reader, pst


def _index_pst(file_key: str) -> list[dict[str, Any]]:
    """Index a PST into ordered, resumable per-message locators (no EML build)."""
    reader, pst = _open_pst(file_key)
    try:
        assert_pst_readable(pst)
        special_folder_map = build_special_folder_map(pst)
        return collect_pst_message_index(pst, special_folder_map)
    finally:
        pst.close()
        reader.close()


def _deliver_pst(
    eml_bytes: bytes,
    recipient_email: str,
    channel: models.Channel,
    imap_labels: list[str],
    imap_flags: list[str],
    is_sender: bool,
) -> bool:
    """Parse + deliver one reconstructed PST message. Returns success."""
    if len(eml_bytes) > settings.MAX_INCOMING_EMAIL_SIZE:
        logger.warning(
            "import: skipping oversized pst message (%d bytes)", len(eml_bytes)
        )
        return False
    parsed_email = parse_email(eml_bytes)
    if parsed_email is None:
        logger.warning("import: skipping unparseable pst message")
        return False
    return bool(
        deliver_inbound_message(
            recipient_email,
            parsed_email,
            eml_bytes,
            is_import=True,
            is_import_sender=is_sender,
            imap_labels=imap_labels,
            imap_flags=imap_flags,
            channel=channel,
            force_lock=True,
        )
    )


def _process_pst_batch(
    file_key: str,
    locators: list[dict[str, Any]],
    recipient: models.Mailbox,
    channel: models.Channel,
) -> tuple[int, int]:
    """Re-open the PST, resolve this batch's locators and deliver each message."""
    reader, pst = _open_pst(file_key)
    success = failure = 0
    recipient_email = str(recipient)
    try:
        assert_pst_readable(pst)
        special_folder_map = build_special_folder_map(pst)
        store_email = get_store_owner_email(pst)
        folder_map = build_pst_folder_map(pst, special_folder_map)
        for loc in locators:
            try:
                eml_bytes = reconstruct_eml_for_locator(
                    folder_map,
                    loc,
                    store_email=store_email,
                    recipient_email=recipient_email,
                )
                if eml_bytes is None:
                    failure += 1
                    continue
                imap_labels, imap_flags, is_sender = compute_pst_labels_flags(
                    loc["folder_type"],
                    loc["folder_path"],
                    loc.get("flags") or 0,
                    loc.get("flag_status"),
                )
                if _deliver_pst(
                    eml_bytes,
                    recipient_email,
                    channel,
                    imap_labels,
                    imap_flags,
                    is_sender,
                ):
                    success += 1
                else:
                    failure += 1
            except Exception:
                logger.exception("import: error processing pst message in %s", file_key)
                failure += 1
    finally:
        pst.close()
        reader.close()
    return success, failure


# --- IMAP ------------------------------------------------------------------


def _imap_credentials(channel: models.Channel) -> dict[str, Any]:
    """Pull the IMAP credentials stored (encrypted) on the import channel."""
    creds = (channel.encrypted_settings or {}).get("imap")
    if not creds:
        raise ValueError(f"IMAP import channel {channel.id} has no stored credentials")
    return creds


def _index_imap(channel: models.Channel) -> list[dict[str, Any]]:
    """Enumerate the IMAP account into ordered, resumable per-message locators.

    Connects once, lists selectable folders, and records each folder's
    UIDVALIDITY plus every message UID. Locators carry
    ``(folder, display_name, uidvalidity, uid)`` — never credentials, which
    stay encrypted on the channel and are re-read fresh by each batch. On a
    re-index (reaper / redelivery) a folder whose UIDVALIDITY changed raises
    ``ImapUidValidityChanged`` so stale UIDs are never reused.
    """
    creds = _imap_credentials(channel)
    run = (channel.settings or {}).get("import", {})
    prior = {
        f["name"]: f["uidvalidity"]
        for f in ((run.get("imap") or {}).get("folders") or [])
    }

    locators: list[dict[str, Any]] = []
    folders_meta: list[dict[str, Any]] = []
    with IMAPConnectionManager(
        creds["imap_server"],
        creds["imap_port"],
        creds["username"],
        creds["password"],
        creds["use_ssl"],
    ) as conn:
        folders = sorted(
            get_selectable_folders(conn, creds["username"], creds["imap_server"])
        )
        mapping = create_folder_mapping(
            folders, creds["username"], creds["imap_server"]
        )
        for folder in folders:
            uidvalidity = get_folder_uidvalidity(conn, folder)
            if uidvalidity is None:
                logger.warning(
                    "import: skipping IMAP folder %s (no UIDVALIDITY)", folder
                )
                continue
            if folder in prior and prior[folder] != uidvalidity:
                raise ImapUidValidityChanged(
                    f"folder {folder!r}: {prior[folder]} -> {uidvalidity}"
                )
            uids = uid_search_all(conn, folder)
            display_name = mapping.get(folder, folder)
            folders_meta.append(
                {
                    "name": folder,
                    "display_name": display_name,
                    "uidvalidity": uidvalidity,
                    "uid_count": len(uids),
                }
            )
            for uid in uids:
                locators.append(
                    {
                        "folder": folder,
                        "display_name": display_name,
                        "uidvalidity": uidvalidity,
                        "uid": uid,
                    }
                )
    # Persist folder metadata so a later re-index can detect UIDVALIDITY drift.
    update_import_state(str(channel.id), imap={"folders": folders_meta})
    return locators


def _process_imap_batch(
    locators: list[dict[str, Any]],
    recipient: models.Mailbox,
    channel: models.Channel,
) -> tuple[int, int]:
    """Reconnect, verify each folder's UIDVALIDITY, then UID-FETCH + deliver."""
    creds = _imap_credentials(channel)
    recipient_email = str(recipient)
    username = creds["username"]
    success = failure = 0

    # Group by folder so we SELECT (and re-verify UIDVALIDITY) once per folder.
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for loc in locators:
        by_folder.setdefault(loc["folder"], []).append(loc)

    with IMAPConnectionManager(
        creds["imap_server"],
        creds["imap_port"],
        username,
        creds["password"],
        creds["use_ssl"],
    ) as conn:
        for folder, locs in by_folder.items():
            expected = locs[0]["uidvalidity"]
            live = get_folder_uidvalidity(conn, folder)
            if live != expected:
                # Stale UIDs: refuse the folder rather than import wrong mail.
                logger.error(
                    "import: UIDVALIDITY changed for %s (%s != %s); skipping slice",
                    folder,
                    expected,
                    live,
                )
                failure += len(locs)
                continue
            if not select_imap_folder(conn, folder):
                logger.error("import: could not select IMAP folder %s", folder)
                failure += len(locs)
                continue
            display_name = locs[0]["display_name"]
            for loc in locs:
                try:
                    flags, raw_email = uid_fetch_message(conn, loc["uid"])
                    if len(raw_email) > settings.MAX_INCOMING_EMAIL_SIZE:
                        logger.warning("import: skipping oversized IMAP message")
                        failure += 1
                        continue
                    parsed_email = parse_email(raw_email)
                    if parsed_email is None:
                        logger.warning("import: skipping unparseable IMAP message")
                        failure += 1
                        continue
                    # Guard the empty-From case (the monolith does not, and
                    # crashes on .lower()): treat a missing sender as "not me".
                    sender_email = first_address_email(parsed_email.get("from")) or ""
                    is_sender = sender_email.lower() == username.lower()
                    if deliver_inbound_message(
                        recipient_email,
                        parsed_email,
                        raw_email,
                        is_import=True,
                        is_import_sender=is_sender,
                        imap_labels=[display_name],
                        imap_flags=flags,
                        channel=channel,
                        force_lock=True,
                    ):
                        success += 1
                    else:
                        failure += 1
                except Exception:
                    logger.exception(
                        "import: error processing IMAP uid %s in %s",
                        loc.get("uid"),
                        folder,
                    )
                    failure += 1
    return success, failure


def _process_batch(
    source_type: str,
    file_key: str,
    locators: list[dict[str, Any]],
    recipient: models.Mailbox,
    channel: models.Channel,
) -> tuple[int, int]:
    """Process one batch's locators for ``source_type``. Returns (success, fail)."""
    if source_type == enums.ImportSource.MBOX.value:
        return _process_mbox_batch(file_key, locators, recipient, channel)
    if source_type == enums.ImportSource.EML.value:
        return _process_eml_batch(file_key, locators, recipient, channel)
    if source_type == enums.ImportSource.PST.value:
        return _process_pst_batch(file_key, locators, recipient, channel)
    if source_type == enums.ImportSource.IMAP.value:
        return _process_imap_batch(locators, recipient, channel)
    raise ValueError(f"Orchestrator does not support source_type={source_type!r}")


def _chunk(plan: list, size: int) -> list[list]:
    """Split a flat plan into fixed-size batches."""
    return [plan[i : i + size] for i in range(0, len(plan), size)]


def _redispatch_pending(channel: models.Channel) -> int:
    """(Re)dispatch a running import's not-yet-completed batches; finalize the
    run if none remain. Returns how many batches were re-dispatched.

    Shared by the reaper (stalled imports) and ``start_import_task`` (when it is
    redelivered after already indexing), so re-running never resets progress.
    The plan is rebuilt deterministically, so a batch number maps to the same
    messages as the original run.
    """
    run = (channel.settings or {}).get("import", {})
    source_type = run.get("source_type")
    if source_type not in SUPPORTED_SOURCES:
        return 0
    file_key = run.get("file_key")
    completed = set(run.get("completed_batches") or [])
    try:
        plan = _build_message_plan(source_type, file_key, channel)
    except ImapUidValidityChanged as exc:
        # The mailbox changed under us; resuming would import the wrong
        # messages. Fail loudly instead of silently re-dispatching forever.
        logger.error("import: UIDVALIDITY changed for channel %s: %s", channel.id, exc)
        mark_finished(
            str(channel.id),
            status=enums.ImportStatus.FAILED.value,
            success_count=run.get("success_count") or 0,
            failure_count=run.get("failure_count") or 0,
            error=str(exc),
        )
        return 0
    except Exception:
        logger.exception("import: re-index failed for channel %s", channel.id)
        return 0

    batch_size = run.get("batch_size") or settings.MESSAGES_IMPORT_BATCH_SIZE
    batches = _chunk(plan, batch_size)
    # Bump heartbeat (so a slow re-dispatch isn't reaped again) and keep the
    # persisted plan size in sync.
    update_import_state(
        str(channel.id),
        heartbeat=timezone.now().isoformat(),
        total_messages=len(plan),
        total_batches=len(batches),
    )

    missing = [n for n in range(len(batches)) if n not in completed]
    if not missing:
        # Every batch is done but the run never flipped to completed (e.g. the
        # finalizing batch crashed after delivering): finalize it now.
        if run.get("status") == enums.ImportStatus.RUNNING.value:
            mark_finished(
                str(channel.id),
                status=enums.ImportStatus.COMPLETED.value,
                success_count=run.get("success_count") or 0,
                failure_count=run.get("failure_count") or 0,
                total_messages=len(plan),
            )
            scrub_import_credentials(str(channel.id))
        return 0

    for batch_number in missing:
        process_import_batch_task.delay(
            str(channel.id), batch_number, batches[batch_number]
        )
    return len(missing)


@celery_app.task(bind=True)
def start_import_task(self, channel_id: str) -> dict[str, Any]:
    """Index the source, persist the plan size, and fan out the batches."""
    channel = get_import_channel(channel_id)
    if channel is None:
        logger.error("start_import_task: import channel %s not found", channel_id)
        return {"status": "FAILURE", "error": "import channel not found"}

    run = (channel.settings or {}).get("import", {})
    # Idempotent re-run: a redelivery after we already indexed must not reset
    # progress — just re-dispatch whatever batches are left.
    if run.get("status") == enums.ImportStatus.RUNNING.value and run.get(
        "total_batches"
    ):
        redispatched = _redispatch_pending(channel)
        return {"status": "RESUMED", "redispatched": redispatched}

    source_type = run.get("source_type")
    file_key = run.get("file_key")

    try:
        plan = _build_message_plan(source_type, file_key, channel)
    except Exception as exc:
        logger.exception("start_import_task: indexing failed for %s", channel_id)
        mark_finished(
            channel_id,
            status=enums.ImportStatus.FAILED.value,
            success_count=0,
            failure_count=0,
            error=str(exc),
        )
        return {"status": "FAILURE", "error": str(exc)}

    batch_size = settings.MESSAGES_IMPORT_BATCH_SIZE
    batches = _chunk(plan, batch_size)
    now = timezone.now().isoformat()
    update_import_state(
        channel_id,
        status=enums.ImportStatus.RUNNING.value,
        started_at=now,
        heartbeat=now,
        total_messages=len(plan),
        total_batches=len(batches),
        batch_size=batch_size,
        completed_batches=[],
    )

    if not batches:
        mark_finished(
            channel_id,
            status=enums.ImportStatus.COMPLETED.value,
            success_count=0,
            failure_count=0,
            total_messages=0,
        )
        scrub_import_credentials(channel_id)
        return {"status": "SUCCESS", "total_messages": 0, "total_batches": 0}

    for batch_number, locators in enumerate(batches):
        process_import_batch_task.delay(channel_id, batch_number, locators)

    return {
        "status": "STARTED",
        "total_messages": len(plan),
        "total_batches": len(batches),
    }


@celery_app.task(bind=True)
def process_import_batch_task(
    self, channel_id: str, batch_number: int, locators: list[dict[str, Any]]
) -> dict[str, Any]:
    """Process one batch and record its completion (idempotent)."""
    channel = get_import_channel(channel_id)
    if channel is None:
        logger.error("process_import_batch_task: channel %s not found", channel_id)
        return {"status": "FAILURE", "error": "import channel not found"}

    run = (channel.settings or {}).get("import", {})
    if run.get("status") == enums.ImportStatus.CANCELLED.value:
        return {"status": "CANCELLED"}
    if batch_number in (run.get("completed_batches") or []):
        return {"status": "ALREADY_DONE", "batch_number": batch_number}

    source_type = run.get("source_type")
    file_key = run.get("file_key")
    recipient = channel.mailbox

    # Per-batch deferrers: reindex/stats are flushed for this batch's threads
    # only, keeping each flush bounded instead of accumulating the whole import.
    with ThreadReindexDeferrer.defer(), ThreadStatsUpdateDeferrer.defer():
        success, failure = _process_batch(
            source_type, file_key, locators, recipient, channel
        )

    finalized = record_batch_completion(
        channel_id,
        batch_number=batch_number,
        success_count=success,
        failure_count=failure,
    )
    if finalized:
        # Run is over: the reaper will never need to reconnect, so drop creds.
        scrub_import_credentials(channel_id)
    return {
        "status": "SUCCESS",
        "batch_number": batch_number,
        "success": success,
        "failure": failure,
        "finalized": finalized,
    }


@celery_app.task(bind=True)
def reap_stalled_imports_task(self) -> dict[str, int]:
    """Re-dispatch the not-yet-completed batches of any stalled running import.

    Runs on the ``default`` queue (not ``imports``) so it can re-dispatch even
    when the single sequential imports worker is busy or stuck on a batch.
    """
    threshold = timezone.now() - timedelta(
        seconds=settings.MESSAGES_IMPORT_STALL_TIMEOUT
    )
    candidates = models.Channel.objects.filter(
        type=enums.ChannelTypes.IMPORT.value,
        settings__import__status=enums.ImportStatus.RUNNING.value,
    )

    redispatched = 0
    for channel in candidates:
        run = (channel.settings or {}).get("import", {})
        heartbeat = run.get("heartbeat")
        try:
            last_beat = datetime.fromisoformat(heartbeat) if heartbeat else None
        except (TypeError, ValueError):
            last_beat = None
        if last_beat and last_beat > threshold:
            continue  # still fresh

        redispatched += _redispatch_pending(channel)

    return {"redispatched": redispatched}

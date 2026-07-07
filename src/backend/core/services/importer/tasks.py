"""Orchestration for imports: the Celery tasks + per-source dispatch.

``run_import_task(channel_id)`` is the single entry point an import channel
dispatches. It loads the channel, guards it (active + single-writer lock), reads
the resume watermark from Redis, and hands off to the source's runner
(``mbox``/``eml``/``pst``/``imap``, via ``_RUNNERS``). Each runner does one
sequential, resumable pass — stamping every delivered message with the import
channel and checkpointing its watermark — using the shared primitives in
``utils`` (``deliver``/``beat``/``imports_storage``).

Resuming is always safe because ``deliver_inbound_message`` dedups on ``mime_id``
(and, for header-less messages, on the raw-bytes sha256): re-running the boundary
message creates no duplicate. The watermark is an *efficiency* device — losing it
(Redis eviction) just means resume-from-start.

A run is single-writer (``acquire_run_lock``) and gated on ``is_active`` so a
finished oneshot is never re-run. The 5-minute reaper (``schedule_imports_task``)
re-dispatches any ``is_active=True`` import whose ``last_used_at`` heartbeat went
stale — which doubles as the poll clock for ``continuous`` IMAP channels.
"""

# pylint: disable=broad-exception-caught, unused-argument
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from botocore.exceptions import BotoCoreError, ClientError
from celery.utils.log import get_task_logger

from core import enums, models

from messages.celery_app import app as celery_app

from .channel import (
    ImportCancelled,
    acquire_run_lock,
    cancel_import,
    clear_state,
    get_import_channel,
    is_cancel_requested,
    mark_finished,
    purge_import_messages,
    read_state,
    release_run_lock,
    write_state,
)
from .eml import run_eml
from .imap import run_imap
from .mbox import run_mbox
from .pst import run_pst
from .utils import TransientImportError, error_text

logger = get_task_logger(__name__)


_RUNNERS = {
    enums.ImportSource.MBOX.value: run_mbox,
    enums.ImportSource.EML.value: run_eml,
    enums.ImportSource.PST.value: run_pst,
    enums.ImportSource.IMAP.value: run_imap,
}

# Consecutive stuck re-dispatches (same watermark, zero forward progress)
# tolerated before a run is declared FAILED, per source. File sources ride out a
# brief storage/S3 blip; IMAP is sized from the poll cadence so a continuous
# poller survives a multi-day server outage (~5 days of polls). Any forward
# progress resets the count. Resolved at import — the poll interval is fixed at
# startup.
FILE_STUCK_RETRIES = 3
IMAP_STUCK_TIMEOUT = 5 * 24 * 3600  # seconds (~5 days)
STUCK_RETRY_LIMITS = {
    enums.ImportSource.MBOX.value: FILE_STUCK_RETRIES,
    enums.ImportSource.EML.value: FILE_STUCK_RETRIES,
    enums.ImportSource.PST.value: FILE_STUCK_RETRIES,
    enums.ImportSource.IMAP.value: IMAP_STUCK_TIMEOUT
    // settings.MESSAGES_IMPORT_IMAP_POLL_INTERVAL,
}


def _finish_cancelled_run(channel: models.Channel) -> None:
    """Post-purge: delete the row of a settled cancelled run.

    A cancel makes the import disappear from ``/imports/`` entirely — its
    messages are already purged, so the row has nothing left to describe.
    Deleted only once the durable CANCELLED write has landed: a cancel seen
    flag-only (the API's ``mark_cancelled`` still in flight) leaves the row
    alone — deleting it here would race the API's own save — and the API's
    ``cancel_import_task`` finishes the removal as soon as the run lock is
    free.
    """
    try:
        channel.refresh_from_db(fields=["settings"])
    except models.Channel.DoesNotExist:
        return
    status = (channel.settings or {}).get("import", {}).get("status")
    if status == enums.ImportStatus.CANCELLED.value:
        clear_state(channel.id)
        channel.delete()


@celery_app.task(bind=True)
def run_import_task(self, channel_id: str) -> dict[str, Any]:
    """Run (or resume) one import to completion. Idempotent + resumable."""
    channel = get_import_channel(channel_id)
    if channel is None:
        logger.error("run_import_task: import channel %s not found", channel_id)
        return {"status": "NOT_FOUND"}
    # A finished oneshot (is_active=False) must never be re-run by a stray
    # reaper tick or retry.
    if not channel.is_active:
        return {"status": "INACTIVE"}
    # Single writer per import: a poll and a crash-recovery can't overlap.
    if not acquire_run_lock(channel_id):
        return {"status": "ALREADY_RUNNING"}

    # Claim the run by advancing the heartbeat now (unthrottled): this both
    # keeps the scheduler from re-dispatching a live run and, for a continuous
    # poll that imports nothing new, still moves ``last_used_at`` forward so the
    # next poll is one interval away rather than every scheduler tick.
    channel.mark_used(only_if_older_than_seconds=0)

    source_type = (channel.settings or {}).get("import", {}).get("source_type")
    runner = _RUNNERS.get(source_type)
    if runner is None:
        release_run_lock(channel_id)
        logger.error("run_import_task: unknown source_type %r", source_type)
        mark_finished(
            channel,
            status=enums.ImportStatus.FAILED.value,
            success=0,
            failure=0,
            error=f"unknown source_type {source_type!r}",
        )
        return {"status": "FAILURE", "error": "unknown source_type"}

    try:
        state = read_state(channel_id)
        try:
            success, failure, total = runner(channel, state)
        except (BotoCoreError, ClientError) as exc:
            # File runners read the archive straight from S3; a storage blip
            # should leave the run resumable (as ``run_imap`` does for its socket
            # errors) rather than terminally fail on the first hiccup.
            raise TransientImportError(f"storage error: {error_text(exc)}") from exc
        # A full pass just completed: any accumulated stuck budget is
        # stale. Without this reset a quiet continuous poller (whose progress
        # marker never changes) would sum unrelated transient blips weeks apart
        # into a permanent FAILED.
        if read_state(channel_id).get("stuck_count"):
            write_state(channel_id, stuck_marker=None, stuck_count=0)
        # Re-read the durable row before declaring the run complete. A cancel (or
        # pause) flips is_active/status in the DB, and the Redis cancel flag can
        # be evicted under memory pressure — so trust the durable CANCELLED
        # status, not only the ephemeral flag, or an evicted flag would let a
        # cancelled import overwrite CANCELLED with COMPLETED.
        try:
            channel.refresh_from_db(fields=["is_active", "settings"])
        except models.Channel.DoesNotExist:
            # Deleted mid-run (e.g. from the admin): Message.channel is
            # SET_NULL so the delivered mail is already unlinked — there is
            # nothing left to mark or purge.
            logger.warning("run_import_task: import %s deleted mid-run", channel_id)
            return {"status": "NOT_FOUND"}
        durable_status = (channel.settings or {}).get("import", {}).get("status")
        if (
            is_cancel_requested(channel_id)
            or durable_status == enums.ImportStatus.CANCELLED.value
        ):
            logger.info(
                "run_import_task: import %s cancelled at completion", channel_id
            )
            purge_import_messages(channel)
            _finish_cancelled_run(channel)
            return {"status": "CANCELLED"}
        mode = (channel.settings or {}).get("import", {}).get("mode")
        if (
            total
            and failure
            and not success
            and mode != enums.ImportMode.CONTINUOUS.value
        ):
            # A oneshot run that delivered nothing is a failure, not a quiet
            # "completed" with error=null: surface an explanation the UI can
            # show (the old per-format tasks returned one). A continuous poll
            # is exempt — a transient bad batch must not disable the poller.
            error = (
                f"None of the {failure} message(s) could be imported. The file "
                "may be corrupt, unparseable, or over the size limit."
            )
            mark_finished(
                channel,
                status=enums.ImportStatus.FAILED.value,
                success=success,
                failure=failure,
                total=total,
                error=error,
            )
            return {"status": "FAILURE", "error": error}
        mark_finished(
            channel,
            status=enums.ImportStatus.COMPLETED.value,
            success=success,
            failure=failure,
            total=total,
        )
        return {
            "status": "SUCCESS",
            "success": success,
            "failure": failure,
            "total": total,
        }
    except ImportCancelled:
        # Cancelled mid-run: the API already wrote the CANCELLED terminal status.
        # Purge anything this run delivered *after* the API's own purge snapshot
        # so no orphaned messages survive the cancel.
        logger.info("run_import_task: import %s cancelled mid-run", channel_id)
        purge_import_messages(channel)
        _finish_cancelled_run(channel)
        return {"status": "CANCELLED"}
    except TransientImportError as exc:
        # Recoverable — leave the channel active + resumable (watermark already
        # persisted below the failed item) so the scheduler re-dispatches it —
        # but under a cross-run budget: too many consecutive re-dispatches with
        # stuck at the same watermark mean the error is permanent, not
        # transient. The budget is much larger for IMAP (ride out a multi-day
        # server outage) than for file/S3 (a quick storage blip).
        st = read_state(channel_id)
        marker = [
            st.get("success", 0),
            st.get("failure", 0),
            st.get("cursor"),
            st.get("folders"),
        ]
        stuck = st.get("stuck_count", 0) + 1 if marker == st.get("stuck_marker") else 1
        write_state(channel_id, stuck_marker=marker, stuck_count=stuck)
        limit = STUCK_RETRY_LIMITS[source_type]
        if stuck >= limit:
            error = f"{exc} — gave up after {stuck} runs stuck at the same position."
            logger.warning("run_import_task: import %s failed: %s", channel_id, error)
            mark_finished(
                channel,
                status=enums.ImportStatus.FAILED.value,
                success=st.get("success", 0),
                failure=st.get("failure", 0),
                total=st.get("total"),
                error=error,
            )
            return {"status": "FAILURE", "error": error}
        logger.warning(
            "run_import_task: import %s paused on transient error (stuck %d/%d): %s",
            channel_id,
            stuck,
            limit,
            exc,
        )
        return {"status": "RETRY", "error": str(exc)}
    except Exception as exc:
        logger.exception("run_import_task: import %s failed", channel_id)
        # Same stale-row hazard as the success path: a cancel may have landed
        # while the runner was failing. Re-check the durable status before
        # writing FAILED so a cancelled run stays cancelled (and anything it
        # delivered after the API's purge snapshot is cleaned up).
        try:
            channel.refresh_from_db(fields=["is_active", "settings"])
        except models.Channel.DoesNotExist:
            # Same deleted-mid-run guard as the success path.
            logger.warning("run_import_task: import %s deleted mid-run", channel_id)
            return {"status": "NOT_FOUND"}
        durable_status = (channel.settings or {}).get("import", {}).get("status")
        if (
            is_cancel_requested(channel_id)
            or durable_status == enums.ImportStatus.CANCELLED.value
        ):
            logger.info(
                "run_import_task: import %s cancelled during failure", channel_id
            )
            purge_import_messages(channel)
            _finish_cancelled_run(channel)
            return {"status": "CANCELLED"}
        st = read_state(channel_id)
        error = error_text(exc)
        mark_finished(
            channel,
            status=enums.ImportStatus.FAILED.value,
            success=st.get("success", 0),
            failure=st.get("failure", 0),
            total=st.get("total"),
            error=error,
        )
        return {"status": "FAILURE", "error": error}
    finally:
        release_run_lock(channel_id)


@celery_app.task(bind=True)
def cancel_import_task(self, channel_id: str) -> dict[str, int]:
    """Delete a cancelled import's messages + clean orphan threads off-request,
    then remove the run's row so it disappears from ``/imports/``.

    The API already flipped the run to ``cancelled`` (``mark_cancelled``); this
    does the potentially-large deletion in the background. Idempotent — safe to
    retry or re-run.
    """
    channel = get_import_channel(channel_id)
    if channel is None:
        return {"messages_deleted": 0, "messages_kept": 0, "threads_deleted": 0}
    try:
        result = cancel_import(channel)
    except models.Channel.DoesNotExist:
        # The live worker finished its own cancel handling (purge + row
        # deletion) between our load and here — nothing left to do.
        return {"messages_deleted": 0, "messages_kept": 0, "threads_deleted": 0}
    # Remove the row only once the run is settled: a worker still holding the
    # run lock is mid-abort, and deleting now would orphan the messages it
    # delivers before unwinding — it deletes the row itself after its own purge
    # (``_finish_cancelled_run``). A crashed worker's lock self-expires, and a
    # row it left behind stays harmless (is_active=False, hidden by the UI).
    if acquire_run_lock(channel_id):
        try:
            _finish_cancelled_run(channel)
        finally:
            release_run_lock(channel_id)
    return result


@celery_app.task(bind=True)
def schedule_imports_task(self) -> dict[str, int]:
    """Dispatch every active import that is due to run.

    "Due" means the durable ``last_used_at`` heartbeat is stale: for a oneshot
    that is a crashed run (stale beyond ``MESSAGES_IMPORT_STALL_TIMEOUT``); for
    a continuous IMAP channel it is the poll clock (stale beyond the global
    ``MESSAGES_IMPORT_IMAP_POLL_INTERVAL`` seconds), so the same scan drives both
    crash-recovery and periodic polling. ``run_import_task`` is idempotent and
    lock-guarded, so an unconditional dispatch is safe.
    """
    now = timezone.now()
    # The due-check runs in one SQL query: ``channel_type_active_idx`` narrows
    # to the active imports and the staleness/mode residual filters there, so
    # only the due ids reach Python (no full-row fetch, JSON parse or
    # credential decrypt per channel). ``last_used_at`` itself is deliberately
    # unindexed — see the model Meta. Both cadences are global settings,
    # type-validated at boot (``values.PositiveIntegerValue``).
    is_continuous = Q(settings__import__mode=enums.ImportMode.CONTINUOUS.value)
    due = (
        Q(last_used_at__isnull=True)
        | (
            is_continuous
            & Q(
                last_used_at__lte=now
                - timedelta(seconds=settings.MESSAGES_IMPORT_IMAP_POLL_INTERVAL)
            )
        )
        | (
            ~is_continuous
            & Q(
                last_used_at__lte=now
                - timedelta(seconds=settings.MESSAGES_IMPORT_STALL_TIMEOUT)
            )
        )
    )
    due_ids = (
        models.Channel.objects.filter(
            type=enums.ChannelTypes.IMPORT.value, is_active=True
        )
        .filter(due)
        .values_list("id", flat=True)
    )
    # Stale heartbeat: either a crashed run to resume or a continuous poll
    # that's due. We do NOT force-release the run lock — a genuinely crashed
    # holder's lock self-expires within the stall window (lock TTL == stall),
    # while a live-but-slow run keeps renewing it (see ``beat``) so a redundant
    # dispatch simply bails on ``ALREADY_RUNNING``. That closes the window
    # where force-releasing a live run's lock let a second runner double-write
    # the same channel.
    redispatched = 0
    for channel_id in due_ids:
        # A broker hiccup on one dispatch must not abort the rest of the scan.
        try:
            run_import_task.delay(str(channel_id))
            redispatched += 1
        except Exception:
            logger.exception(
                "schedule_imports_task: dispatch failed for %s", channel_id
            )
    return {"redispatched": redispatched}

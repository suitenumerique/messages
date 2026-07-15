"""Model an import run as a ``Channel`` (type=import).

Every message an import creates carries the channel FK (``Message.channel``),
which makes the run trackable and cancellable. The split between where state
lives is deliberate:

* **Durable, on the Channel row** — ``settings["import"]`` holds the *config*
  needed to (re)start a run (``source_type``, ``file_key``, ``mode``) plus a
  *terminal snapshot* (``status`` + final counts) written once when the run
  ends, so ``/imports/`` survives a Redis eviction. IMAP credentials live in
  ``encrypted_settings``. ``is_active`` is the terminal flag (True while a run
  may still make progress; flipped False when a oneshot finishes / a channel
  is disabled). ``last_used_at`` is the throttled heartbeat, updated via the
  model's own ``mark_used()``.
* **Ephemeral, in Redis** (``import:{id}``) — the fast-changing progress:
  live status, counts, and the resume *watermark* (a positional ``cursor`` for
  file sources, per-folder ``{uidvalidity,last_uid}`` for IMAP). Losing it is
  safe: the run resumes from the start and dedup (mime_id, else blob sha256)
  keeps re-delivery idempotent.

Credentials are never scrubbed on completion — they stay (encrypted) for the
channel's life so a oneshot can be re-enabled as continuous later, and are
freed only when the channel row is deleted.
"""

import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from core import enums, models

logger = logging.getLogger(__name__)

# How long a run's live Redis state sticks around after its last write. Long
# enough to outlast any real import; eviction only costs a resume-from-start.
STATE_TTL = 7 * 24 * 3600

# Statuses past which a oneshot run no longer changes.
TERMINAL_STATUSES = frozenset(
    {
        enums.ImportStatus.COMPLETED.value,
        enums.ImportStatus.FAILED.value,
        enums.ImportStatus.CANCELLED.value,
    }
)


class ImportCancelled(Exception):
    """Raised inside a runner when a cancel was requested mid-run so the loop
    unwinds and ``run_import_task`` skips the normal terminal transition."""


def _state_key(channel_id: Any) -> str:
    return f"import:{channel_id}"


def _lock_key(channel_id: Any) -> str:
    return f"import:{channel_id}:lock"


def _cancel_key(channel_id: Any) -> str:
    return f"import:{channel_id}:cancel"


def create_import_channel(
    *,
    recipient: models.Mailbox,
    user,
    source_type: str,
    file_key: str | None = None,
    name: str | None = None,
    imap_credentials: dict[str, Any] | None = None,
    mode: str = enums.ImportMode.ONESHOT.value,
) -> models.Channel:
    """Create the Channel that groups an import run.

    ``scope_level=MAILBOX`` bound to ``recipient`` (``user`` is the creator
    audit). Starts ``is_active=True`` (the run is live); the durable config
    goes into ``settings["import"]`` and IMAP credentials, when provided, into
    ``encrypted_settings``. A ``mode=continuous`` run polls on the global
    ``MESSAGES_IMPORT_IMAP_POLL_INTERVAL`` cadence.
    """
    run: dict[str, Any] = {
        "source_type": source_type,
        "mode": mode,
        "file_key": file_key,
        # Terminal snapshot fields, filled in by ``mark_finished`` so the
        # resource still reads correctly after the Redis state is evicted.
        "status": enums.ImportStatus.PENDING.value,
    }
    encrypted_settings: dict[str, Any] = {}
    if imap_credentials:
        encrypted_settings["imap"] = imap_credentials

    return models.Channel.objects.create(
        name=name or f"Import {source_type}",
        type=enums.ChannelTypes.IMPORT.value,
        scope_level=enums.ChannelScopeLevel.MAILBOX,
        mailbox=recipient,
        user=user,
        is_active=True,
        settings={"import": run},
        encrypted_settings=encrypted_settings,
    )


def get_import_channel(channel_id: Any) -> models.Channel | None:
    """Load an import Channel by id, or ``None`` (missing id / wrong type)."""
    if not channel_id:
        return None
    return models.Channel.objects.filter(
        id=channel_id, type=enums.ChannelTypes.IMPORT.value
    ).first()


# --- Redis-backed live state ------------------------------------------------


def read_state(channel_id: Any) -> dict[str, Any]:
    """The run's live progress from Redis (``{}`` if never written / evicted)."""
    return cache.get(_state_key(channel_id)) or {}


def write_state(channel_id: Any, **fields: Any) -> dict[str, Any]:
    """Merge ``fields`` into the live Redis state and return the new dict.

    A single writer per run (guarded by ``acquire_run_lock``) makes the plain
    read-modify-write safe.
    """
    state = read_state(channel_id)
    state.update(fields)
    cache.set(_state_key(channel_id), state, timeout=STATE_TTL)
    return state


def clear_state(channel_id: Any) -> None:
    cache.delete(_state_key(channel_id))
    cache.delete(_cancel_key(channel_id))


def _patch_import_run(channel: models.Channel, **fields: Any) -> dict[str, Any]:
    """Set ``channel.settings["import"][*fields]`` on a fresh copy (no save).

    The one place that rebuilds the nested settings dict — every durable write
    to the run config goes through here so the read-modify-write shape isn't
    re-spelled at each call site. Returns the merged ``import`` sub-dict.
    """
    run = dict((channel.settings or {}).get("import") or {})
    run.update(fields)
    settings_data = dict(channel.settings or {})
    settings_data["import"] = run
    channel.settings = settings_data
    return run


def merged_state(channel: models.Channel) -> dict[str, Any]:
    """Durable config + terminal snapshot overlaid with live Redis progress.

    The serializer reads this: while a run is live the Redis values win; once
    it is over (or its state was evicted) the durable ``settings["import"]``
    snapshot carries status and final counts.
    """
    durable = (channel.settings or {}).get("import", {})
    return {**durable, **read_state(channel.id)}


# --- lifecycle transitions --------------------------------------------------


def mark_started(channel_id: Any, *, total: int | None = None) -> None:
    """Flip the live state to ``running`` and stamp ``started_at``."""
    fields: dict[str, Any] = {
        "status": enums.ImportStatus.RUNNING.value,
        "started_at": timezone.now().isoformat(),
        "error": None,
    }
    if total is not None:
        fields["total"] = total
    write_state(channel_id, **fields)


def record_progress(
    channel_id: Any,
    *,
    success: int,
    failure: int,
    cursor: int | None = None,
    folders: dict[str, Any] | None = None,
    total: int | None = None,
) -> None:
    """Persist a progress tick to Redis (counts + resume watermark)."""
    fields: dict[str, Any] = {"success": success, "failure": failure}
    if cursor is not None:
        fields["cursor"] = cursor
    if folders is not None:
        fields["folders"] = folders
    if total is not None:
        fields["total"] = total
    write_state(channel_id, **fields)


def mark_finished(
    channel: models.Channel,
    *,
    status: str,
    success: int,
    failure: int,
    total: int | None = None,
    error: str | None = None,
) -> None:
    """End a run: write the terminal state to Redis *and* to the durable
    channel snapshot, and (for oneshot) flip ``is_active=False``.

    A ``continuous`` run stays ``is_active=True``: it is "completed" only until
    the next poll re-dispatches it. Credentials are intentionally left in
    place (freed with the channel row on delete).
    """
    # The caller's in-memory row may be stale (a runner loads its channel once,
    # at task start) while a concurrent cancel already wrote its own terminal
    # snapshot. Re-read the durable row before the read-modify-write and never
    # downgrade CANCELLED: resurrecting a pre-cancel status would defeat the
    # durable backstop ``run_import_task`` relies on when the Redis cancel flag
    # has been evicted.
    channel.refresh_from_db(fields=["settings", "is_active"])
    run = dict((channel.settings or {}).get("import") or {})
    if (
        run.get("status") == enums.ImportStatus.CANCELLED.value
        and status != enums.ImportStatus.CANCELLED.value
    ):
        logger.info(
            "mark_finished: import %s already cancelled; not overwriting with %s",
            channel.id,
            status,
        )
        return

    now = timezone.now().isoformat()
    write_state(
        channel.id,
        status=status,
        success=success,
        failure=failure,
        total=total,
        error=error,
        finished_at=now,
    )

    run = _patch_import_run(
        channel,
        status=status,
        success=success,
        failure=failure,
        total=total,
        finished_at=now,
        # Persist the failure reason durably too: the Redis copy is evicted
        # after STATE_TTL, and a silently-disabled continuous poller must
        # still be able to explain *why* it stopped days later.
        error=error,
    )

    is_continuous = run.get("mode") == enums.ImportMode.CONTINUOUS.value
    # A continuous run that merely finished a poll stays active; only a real
    # terminal state (failed/cancelled) disables it.
    stays_active = is_continuous and status == enums.ImportStatus.COMPLETED.value

    update_fields = ["settings"]
    if not stays_active and channel.is_active:
        channel.is_active = False
        update_fields.append("is_active")
    channel.save(update_fields=update_fields)


def heartbeat(channel: models.Channel) -> None:
    """Throttled durable liveness marker; drives the import scheduler."""
    channel.mark_used()


def enable_continuous(channel: models.Channel) -> None:
    """(Re-)arm an import as a continuous poller.

    Sets ``mode=continuous`` and flips ``is_active=True``, resetting the
    heartbeat so the scheduler picks it up on the next tick. Used both to start
    a continuous IMAP channel and to re-enable a finished oneshot as continuous
    later (its credentials were kept, so no re-auth is needed). The poll cadence
    is the global ``MESSAGES_IMPORT_IMAP_POLL_INTERVAL``, not stored per-channel.
    """
    _patch_import_run(channel, mode=enums.ImportMode.CONTINUOUS.value)
    channel.is_active = True
    # Clear the heartbeat so ``schedule_imports_task`` dispatches it promptly.
    channel.last_used_at = None
    channel.save(update_fields=["settings", "is_active", "last_used_at"])
    # A previously *cancelled* run can be re-armed too: drop the stale cancel
    # flag (it outlives the cancel by STATE_TTL) or the first ``beat`` of the
    # new run would insta-cancel it — and purge its messages — on every poll.
    clear_cancel_request(channel.id)


def disable_continuous(channel: models.Channel) -> None:
    """Demote a continuous poller back to a one-shot: ``mode=oneshot`` +
    ``is_active=False``.

    The run keeps its credentials (and whatever watermark still lives in
    Redis), so it can be re-armed later with ``mode=continuous`` — no re-auth,
    and at worst a full re-scan that dedup keeps duplicate-free.
    """
    _patch_import_run(channel, mode=enums.ImportMode.ONESHOT.value)
    channel.is_active = False
    channel.save(update_fields=["settings", "is_active"])


def pause_import(channel: models.Channel) -> None:
    """Disable an import (stop a continuous poller): ``is_active=False``.

    Credentials are kept — they are freed only when the channel is deleted, so
    the poller can be re-enabled later.
    """
    if channel.is_active:
        channel.is_active = False
        channel.save(update_fields=["is_active"])


# --- single-run concurrency guard ------------------------------------------


def _lock_ttl(ttl: int | None) -> int:
    # The lock must NOT outlive the stall window: once a run's heartbeat is
    # stale enough for the scheduler to treat it as dead and re-dispatch, the
    # orphaned lock of a crashed worker has to be gone or the resume would bail
    # on it. So the lock TTL tracks MESSAGES_IMPORT_STALL_TIMEOUT (a hard-killed
    # holder's lock self-expires by the time the run is considered stalled).
    return ttl or settings.MESSAGES_IMPORT_STALL_TIMEOUT


def acquire_run_lock(channel_id: Any, *, ttl: int | None = None) -> bool:
    """Best-effort "only one worker runs this import at a time" lock.

    ``cache.add`` is atomic on Redis (SET NX), so a poll and the scheduler
    can't double-dispatch the same channel. A live run refreshes it via
    ``renew_run_lock`` (so it never expires mid-run); a crashed holder's lock
    self-expires within the stall window so the resume can re-acquire it.
    """
    return bool(cache.add(_lock_key(channel_id), "1", timeout=_lock_ttl(ttl)))


def renew_run_lock(channel_id: Any, *, ttl: int | None = None) -> None:
    """Refresh the run lock's TTL — called on each progress flush so a long,
    healthy run keeps the lock while a crashed one lets it lapse."""
    cache.set(_lock_key(channel_id), "1", timeout=_lock_ttl(ttl))


def release_run_lock(channel_id: Any) -> None:
    cache.delete(_lock_key(channel_id))


# --- cancellation -----------------------------------------------------------


def request_cancel(channel_id: Any) -> None:
    """Raise a fast Redis flag a running runner polls between progress flushes,
    so an in-flight run stops cooperatively instead of running to completion.

    Deliberately its own key, NOT a field of the state dict: the runner's
    plain read-modify-write of that dict (``write_state``) could otherwise
    clobber a cancel that lands between its read and its set.
    """
    cache.set(_cancel_key(channel_id), "1", timeout=STATE_TTL)


def is_cancel_requested(channel_id: Any) -> bool:
    """True once ``request_cancel`` (or ``mark_cancelled``) has been called."""
    return bool(cache.get(_cancel_key(channel_id)))


def clear_cancel_request(channel_id: Any) -> None:
    """Drop a (now stale) cancel flag — called when a run is deliberately
    re-armed, so a previous cancel can't insta-cancel the new run."""
    cache.delete(_cancel_key(channel_id))


def mark_cancelled(channel: models.Channel) -> None:
    """Flip a run to ``cancelled`` (terminal): ``is_active=False`` + status.

    Cheap and synchronous — the API calls this so the run stops and the
    scheduler won't resume it, then offloads the message deletion to
    ``purge_import_messages`` in a background task. Also raises the cooperative
    cancel flag so a run that is *currently executing* aborts at its next flush
    (rather than finishing and overwriting the cancelled status with completed).
    """
    request_cancel(channel.id)
    st = read_state(channel.id)
    mark_finished(
        channel,
        status=enums.ImportStatus.CANCELLED.value,
        success=st.get("success", 0),
        failure=st.get("failure", 0),
    )


def purge_import_messages(channel: models.Channel) -> dict[str, int]:
    """Delete a run's imported messages and clean threads left empty.

    Messages in threads that gathered *non-import* activity since the import —
    a reply that arrived, a draft or sent reply composed in the app — are
    spared: cancelling means "undo the import", but deleting the anchor of a
    live conversation would orphan its replies. Messages from other import
    runs deliberately don't count as activity, so cancelling two overlapping
    imports still cleans everything.

    Idempotent: a second call finds no purgeable messages and is a no-op
    (spared messages stay spared — their thread is still active). Distinct
    from deleting the channel (which keeps the mail — ``Message.channel`` is
    SET_NULL). Orphaned blobs are reclaimed by the periodic GC via the message
    ``post_delete`` signals.
    """
    message_qs = models.Message.objects.filter(channel=channel)
    imported_thread_ids = set(message_qs.values_list("thread_id", flat=True))
    # A thread is "active" when it holds any message that is not import
    # payload: channel is NULL (normal mail, app replies, or mail kept from a
    # forgotten import) or a non-import channel (widget/API-key deliveries).
    active_thread_ids = set(
        models.Message.objects.filter(thread_id__in=imported_thread_ids)
        .filter(
            Q(channel__isnull=True) | ~Q(channel__type=enums.ChannelTypes.IMPORT.value)
        )
        .values_list("thread_id", flat=True)
    )

    purge_qs = message_qs.exclude(thread_id__in=active_thread_ids)
    message_count = purge_qs.count()
    messages_kept = message_qs.count() - message_count
    touched_thread_ids = imported_thread_ids - active_thread_ids

    purge_qs.delete()

    threads_deleted = 0
    if touched_thread_ids:
        _, deleted_by_model = models.Thread.objects.filter(
            id__in=touched_thread_ids, messages__isnull=True
        ).delete()
        threads_deleted = deleted_by_model.get("core.Thread", 0)
        for thread in models.Thread.objects.filter(id__in=touched_thread_ids):
            try:
                thread.update_stats()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Failed to update stats for thread %s after import cancel",
                    thread.id,
                )
    return {
        "messages_deleted": message_count,
        "messages_kept": messages_kept,
        "threads_deleted": threads_deleted,
    }


def cancel_import(channel: models.Channel) -> dict[str, int]:
    """Mark cancelled + purge messages in one call (used by the background
    task and tests). Idempotent."""
    mark_cancelled(channel)
    return purge_import_messages(channel)

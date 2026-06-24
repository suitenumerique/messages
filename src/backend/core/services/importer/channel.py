"""Model an import run as a ``Channel`` (type=import).

Every message created by one import carries the channel FK
(``Message.channel``), which makes the run identifiable and cancellable.
The mutable run state lives in ``channel.settings["import"]`` and IMAP
credentials (when any) in ``channel.encrypted_settings["imap"]``.

State writes go through ``update_import_state`` which uses
``select_for_update`` + a queryset ``.update()``: it serializes concurrent
writers (the split-task pipeline of axis 2), skips ``full_clean`` and does
not rewrite ``encrypted_settings`` (so credentials are never re-encrypted on
a progress tick).
"""

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from core import enums, models

logger = logging.getLogger(__name__)

# Statuses past which a run no longer changes.
TERMINAL_STATUSES = frozenset(
    {
        enums.ImportStatus.COMPLETED,
        enums.ImportStatus.FAILED,
        enums.ImportStatus.CANCELLED,
    }
)


def create_import_channel(
    *,
    recipient: models.Mailbox,
    user,
    source_type: str,
    file_key: str | None = None,
    name: str | None = None,
    imap_credentials: dict[str, Any] | None = None,
    extra_settings: dict[str, Any] | None = None,
) -> models.Channel:
    """Create the Channel that groups an import run.

    ``scope_level=MAILBOX`` bound to ``recipient`` (``user`` is the creator
    audit). The run starts ``pending``; the task flips it to ``running``.
    IMAP credentials, when provided, are stored in ``encrypted_settings``.
    """
    run: dict[str, Any] = {
        "source_type": source_type,
        "status": enums.ImportStatus.PENDING.value,
        "file_key": file_key,
        "success_count": 0,
        "failure_count": 0,
        "total_messages": 0,
        "started_at": None,
        "finished_at": None,
        "error": None,
    }
    if extra_settings:
        run.update(extra_settings)

    encrypted_settings: dict[str, Any] = {}
    if imap_credentials:
        encrypted_settings["imap"] = imap_credentials

    return models.Channel.objects.create(
        name=name or f"Import {source_type}",
        type=enums.ChannelTypes.IMPORT.value,
        scope_level=enums.ChannelScopeLevel.MAILBOX,
        mailbox=recipient,
        user=user,
        settings={"import": run},
        encrypted_settings=encrypted_settings,
    )


def get_import_channel(channel_id: str | None) -> models.Channel | None:
    """Load an import Channel by id, or ``None`` (missing id / wrong type)."""
    if not channel_id:
        return None
    return models.Channel.objects.filter(
        id=channel_id, type=enums.ChannelTypes.IMPORT.value
    ).first()


def update_import_state(channel_id: str | None, **fields: Any) -> None:
    """Merge ``fields`` into ``settings["import"]`` race-free.

    No-op when ``channel_id`` is falsy or the row is gone. Uses
    ``select_for_update`` to serialize concurrent writers and a queryset
    ``.update()`` so neither ``full_clean`` nor the ``encrypted_settings``
    re-encryption runs on the hot path.
    """
    if not channel_id:
        return
    with transaction.atomic():
        channel = (
            models.Channel.objects.select_for_update().filter(id=channel_id).first()
        )
        if channel is None:
            return
        data = dict(channel.settings or {})
        run = dict(data.get("import") or {})
        run.update(fields)
        data["import"] = run
        models.Channel.objects.filter(id=channel_id).update(settings=data)


def mark_started(channel_id: str | None, *, total_messages: int | None = None) -> None:
    """Flip the run to ``running`` and stamp ``started_at``."""
    fields: dict[str, Any] = {
        "status": enums.ImportStatus.RUNNING.value,
        "started_at": timezone.now().isoformat(),
    }
    if total_messages is not None:
        fields["total_messages"] = total_messages
    update_import_state(channel_id, **fields)


def mark_finished(
    channel_id: str | None,
    *,
    status: str,
    success_count: int,
    failure_count: int,
    total_messages: int | None = None,
    error: str | None = None,
) -> None:
    """Stamp the terminal state + final counts on the run."""
    fields: dict[str, Any] = {
        "status": status,
        "success_count": success_count,
        "failure_count": failure_count,
        "finished_at": timezone.now().isoformat(),
        "error": error,
    }
    if total_messages is not None:
        fields["total_messages"] = total_messages
    update_import_state(channel_id, **fields)


def record_batch_completion(
    channel_id: str,
    *,
    batch_number: int,
    success_count: int,
    failure_count: int,
) -> bool:
    """Atomically record a batch as completed and finalize the run if it was
    the last one. Returns True if this call finalized the run.

    The whole read-modify-write runs under ``select_for_update`` so concurrent
    batches can't lose each other's progress, and "am I the last batch?" is
    evaluated in the same locked section — exactly one batch finalizes, with no
    chord callback to lose on a worker crash. A ``cancelled`` run is never
    flipped back to ``completed``.
    """
    finalized = False
    with transaction.atomic():
        channel = (
            models.Channel.objects.select_for_update().filter(id=channel_id).first()
        )
        if channel is None:
            return False
        data = dict(channel.settings or {})
        run = dict(data.get("import") or {})
        completed = list(run.get("completed_batches") or [])
        if batch_number not in completed:
            completed.append(batch_number)
        run["completed_batches"] = completed
        run["success_count"] = (run.get("success_count") or 0) + success_count
        run["failure_count"] = (run.get("failure_count") or 0) + failure_count
        run["heartbeat"] = timezone.now().isoformat()
        total_batches = run.get("total_batches") or 0
        if (
            total_batches
            and len(completed) >= total_batches
            and run.get("status") == enums.ImportStatus.RUNNING.value
        ):
            run["status"] = enums.ImportStatus.COMPLETED.value
            run["finished_at"] = timezone.now().isoformat()
            finalized = True
        data["import"] = run
        models.Channel.objects.filter(id=channel_id).update(settings=data)
    return finalized


def scrub_import_credentials(channel_id: str | None) -> None:
    """Drop any stored IMAP credentials once a run can no longer resume.

    Called when an import reaches a terminal state (completed/cancelled): the
    credentials were only kept to let the reaper reconnect, so there is no
    reason to retain them past the run. A no-op for file imports (no creds).
    """
    channel = get_import_channel(channel_id)
    if channel is not None and channel.encrypted_settings:
        channel.encrypted_settings = {}
        channel.save(update_fields=["encrypted_settings"])


def cancel_import(channel: models.Channel) -> dict[str, int]:
    """Cancel a run: mark cancelled, delete its messages, clean orphan threads.

    Threads created solely by the import (no message left after the delete)
    are removed; threads still holding non-import messages survive and have
    their stats recomputed. Orphaned blobs are reclaimed by the periodic GC
    via the message ``post_delete`` signals. IMAP credentials are scrubbed.
    """
    message_count = models.Message.objects.filter(channel=channel).count()
    # Capture touched threads before the messages disappear.
    touched_thread_ids = set(
        models.Message.objects.filter(channel=channel).values_list(
            "thread_id", flat=True
        )
    )

    models.Message.objects.filter(channel=channel).delete()

    threads_deleted = 0
    if touched_thread_ids:
        # ``QuerySet.delete()`` returns the total across cascades; pull the
        # Thread row count out of the per-model breakdown.
        _, deleted_by_model = models.Thread.objects.filter(
            id__in=touched_thread_ids, messages__isnull=True
        ).delete()
        threads_deleted = deleted_by_model.get("core.Thread", 0)
        # Recompute stats on the threads that survived (shared with
        # non-import messages).
        for thread in models.Thread.objects.filter(id__in=touched_thread_ids):
            try:
                thread.update_stats()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception(
                    "Failed to update stats for thread %s after import cancel",
                    thread.id,
                )

    update_import_state(
        channel.id,
        status=enums.ImportStatus.CANCELLED.value,
        finished_at=timezone.now().isoformat(),
    )
    # The run is over: scrub any stored credentials.
    if channel.encrypted_settings:
        channel.encrypted_settings = {}
        channel.save(update_fields=["encrypted_settings"])

    return {"messages_deleted": message_count, "threads_deleted": threads_deleted}

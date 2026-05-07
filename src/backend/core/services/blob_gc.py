"""
Blob lifecycle: candidate-set GC, upload reservations, and the periodic
sweep task.

Blobs are not owned by a Mailbox/MailDomain via a foreign key — their
lifetime is determined by whichever ``Message`` / ``Attachment`` /
``MessageTemplate`` / ``MailboxBlob`` row references them. CASCADE
delete can't clean blobs up; instead:

- Reference sources push their blob_ids into a Redis set on
  ``post_delete`` (cheap — O(1) SADD, no per-blob celery task even
  when 100k cascade together).
- A periodic Celery task drains the set, checks each candidate for
  remaining references, and deletes orphans (with inline S3 cleanup
  under the per-sha advisory lock — same pattern as
  ``offload_blobs_task``).
- A weekly "full" run walks every Blob row to catch anything that
  fell through (Redis outage, signal that didn't fire, etc.).

The ``MailboxBlob`` model holds the JMAP upload reservation as a
real DB row with an explicit ``expires_at``: ``upload_and_reserve_blob``
creates one when the user uploads, ``release_upload`` drops it once
an Attachment FK takes over, and the GC sweep drops stale rows
(past ``expires_at``) before deleting the blob. Active rows (with
``expires_at > now()``) count as references in
``Blob.objects.is_referenced``, so a reserved blob is naturally
excluded from collection — no Redis needed for the reservation
itself.
"""

# pylint: disable=broad-exception-caught

from time import monotonic
from typing import Any, Dict, Iterable, Iterator
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery.utils.log import get_task_logger
from redis.exceptions import RedisError

from core.enums import BlobStorageLocationChoices
from core.models import UPLOAD_RESERVATION_TTL, Blob, MailboxBlob
from core.services.tiered_storage import TieredStorageService, sha256_advisory_lock

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


# Redis set holding blob ids that may have become orphans. Reference-source
# post_delete signals SADD here; the GC task SPOPs in batches.
_GC_CANDIDATES_KEY = "messages_blobs:gc_candidates"

_GC_FAST_BATCH_SIZE = 1000

# Wall-clock budget for one tick. Hourly schedule, capped at 55 min so
# the task always returns before the next beat tick could overlap.
_GC_MAX_RUN_SECONDS = 55 * 60


# The candidate set needs SADD/SPOP atomicity that Django's pluggable
# cache layer can't deliver across workers, so we only support
# ``django_redis`` here. Other backends skip with a warning; the
# ``--full`` sweep is the safety net for those environments.


def _is_redis_backend() -> bool:
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    return "django_redis" in backend


def _redis_client():
    # Lazy import: django_redis is an optional dependency for environments
    # that don't use Redis. The system check refuses to boot when blob
    # lifecycle features are enabled without it.
    # pylint: disable-next=import-outside-toplevel
    from django_redis import get_redis_connection

    return get_redis_connection("default")


# --------------------------------------------------------------------
# Candidate set
# --------------------------------------------------------------------


def schedule_for_gc(blob_id) -> None:
    """Push a blob id into the GC candidate set.

    Called from ``post_delete`` on Message / Attachment / MessageTemplate
    when they may have been the last reference to a Blob, and from a
    handful of explicit "release this blob" sites in the MDA flows. The
    GC task (later) re-checks the reference graph; producers don't need
    to be accurate, just safe.

    The Redis SADD is wrapped in ``transaction.on_commit`` so two
    things happen automatically:

    - if the surrounding transaction rolls back, no phantom candidate
      is left behind (the row was never deleted, the blob is still
      alive — no need to enqueue);
    - the SADD never lands before the deletion is visible to other
      transactions, which closes a "lost candidate" race where a GC
      tick consumed the candidate, saw the row still alive (commit
      hadn't happened yet), skipped it, and then the deletion
      committed with nobody re-enqueuing it.

    Outside any transaction, ``on_commit`` runs the callback
    immediately, so ad-hoc usage stays correct.

    A Redis outage drops the id; the periodic ``--full`` sweep is the
    safety net for that case.
    """
    if blob_id is None:
        return
    value = str(blob_id)
    if not _is_redis_backend():
        logger.warning(
            "Blob GC candidate set requires Redis: id %s dropped. "
            "Configure django_redis or rely on `--full` periodic sweeps.",
            value,
        )
        return

    def _push():
        try:
            _redis_client().sadd(_GC_CANDIDATES_KEY, value)
        except RedisError as exc:
            logger.error(
                "Redis unavailable while enqueuing blob %s for GC (%s: %s); "
                "id dropped — `--full` sweep will catch it eventually",
                value,
                type(exc).__name__,
                exc,
            )
        except Exception:
            logger.exception("Failed to enqueue %s for GC", value)

    transaction.on_commit(_push)


def _drain_candidates(batch_size: int) -> list[str]:
    """Pop up to ``batch_size`` ids from the candidate set."""
    if not _is_redis_backend():
        return []
    try:
        popped = _redis_client().spop(_GC_CANDIDATES_KEY, count=batch_size)
        return [
            bid.decode() if isinstance(bid, bytes) else str(bid)
            for bid in (popped or [])
        ]
    except RedisError as exc:
        logger.error(
            "Redis unavailable while draining blob GC set (%s: %s); skipping this tick",
            type(exc).__name__,
            exc,
        )
        return []
    except Exception:
        logger.exception("Failed to drain blob GC candidate set")
        return []


# --------------------------------------------------------------------
# Upload reservations (``MailboxBlob`` rows)
# --------------------------------------------------------------------


def upload_and_reserve_blob(mailbox, content: bytes, content_type: str, **kwargs):
    """JMAP upload primitive: dedup-create a Blob and register a
    ``MailboxBlob`` reservation row under ``mailbox``.

    The reservation is load-bearing only for the JMAP two-step
    upload-then-attach window (the client holds ``blob_id`` between
    two HTTP calls); server-side flows that establish the FK in the
    same transaction must use ``Blob.objects.create_blob`` directly
    inside ``transaction.atomic`` instead — atomicity is the
    protection there, not the reservation row.

    The whole create-then-reserve sequence runs inside one
    ``transaction.atomic`` so the dedup hot-path SELECT in
    ``BlobManager.create_blob`` can take ``select_for_update`` on the
    matched row; this prevents a concurrent
    ``gc_orphan_blobs_task`` from racing between the SELECT and the
    ``MailboxBlob`` INSERT. The FK from ``MailboxBlob`` to ``Blob``
    is ``PROTECT``, so once the row is in the DB the GC can't delete
    the blob until the row's ``expires_at`` lapses.

    A re-upload of the same content by the same mailbox refreshes
    ``expires_at`` (UPSERT semantics via ``update_or_create``) rather
    than creating a duplicate row.

    Used by ``BlobViewSet.upload`` (the actual JMAP endpoint) and by
    ``BlobFactory(mailbox=...)`` in tests that simulate uploads.
    """
    with transaction.atomic():
        blob = Blob.objects.create_blob(
            content=content,
            content_type=content_type,
            **kwargs,
        )
        MailboxBlob.objects.update_or_create(
            blob=blob,
            mailbox=mailbox,
            defaults={"expires_at": timezone.now() + UPLOAD_RESERVATION_TTL},
        )
    return blob


def release_upload(blob, mailbox) -> None:
    """Drop the upload reservation for ``(blob, mailbox)``, if any.

    Call after an ``Attachment`` (or any other reference) is created
    pointing at the blob — the reference graph now covers it; the
    reservation row would just keep the blob in the GC's "skip"
    bucket past its useful life. ``expires_at`` would clean up
    regardless; this just shortens the unnecessary-protection
    window.
    """
    if blob is None or mailbox is None:
        return
    MailboxBlob.objects.filter(blob=blob, mailbox=mailbox).delete()


# --------------------------------------------------------------------
# GC task
# --------------------------------------------------------------------


def _all_blob_ids_iterator() -> Iterator[str]:
    """Yield every Blob.id as a string. Used by ``--full`` mode."""
    qs = Blob.objects.values_list("id", flat=True).order_by()
    for blob_id in qs.iterator(chunk_size=1000):
        yield str(blob_id)


def _gc_one_blob(blob_id_str: str, service) -> str:
    """Process a single GC candidate.

    Returns one of ``"deleted"``, ``"skipped_referenced"``,
    ``"not_found"``, ``"error"``. (Active upload reservations are
    handled inside ``Blob.objects.is_referenced``: a ``MailboxBlob``
    row with ``expires_at > now()`` counts as a reference, so
    reserved blobs naturally hit the ``skipped_referenced`` branch.
    Stale reservation rows are deleted in the under-lock block below
    before the blob delete itself.)
    """
    try:
        blob_uuid = UUID(blob_id_str)
    except (TypeError, ValueError):
        logger.warning("GC: dropping non-UUID candidate %r", blob_id_str)
        return "error"

    # Cheap pre-lock probes: is_referenced and a row-existence check.
    # The authoritative answer comes from the under-lock select_for_update
    # block below; these just avoid taking the advisory lock for blobs
    # that are obviously still alive or already gone.
    try:
        sha_pre = bytes(Blob.objects.values_list("sha256", flat=True).get(id=blob_uuid))
    except Blob.DoesNotExist:
        return "not_found"
    if Blob.objects.is_referenced(blob_uuid):
        return "skipped_referenced"

    try:
        with transaction.atomic(), sha256_advisory_lock(sha_pre):
            # Take FOR UPDATE on the Blob row. Postgres takes a
            # FOR KEY SHARE lock on the parent row whenever an FK
            # INSERT references it (Attachment, Message,
            # MessageTemplate, MailboxBlob); FOR UPDATE conflicts
            # with FOR KEY SHARE, so this blocks any concurrent
            # reference creation for the duration of the transaction.
            # Without this, the is_referenced + delete sequence is a
            # TOCTOU window: a concurrent reference insert can
            # commit between the check and the delete. select_for_update
            # plus the re-check below close that window.
            try:
                blob = (
                    Blob.objects.select_for_update()
                    .only("id", "sha256", "encryption_key_id", "storage_location")
                    .get(id=blob_uuid)
                )
            except Blob.DoesNotExist:
                return "not_found"

            # Re-check inside the lock: another GC tick or concurrent
            # path may have already collected this candidate, or a
            # new ``MailboxBlob`` reservation could have been
            # registered while we waited for the lock.
            if Blob.objects.is_referenced(blob_uuid):
                return "skipped_referenced"

            sha = bytes(blob.sha256)
            key_id = blob.encryption_key_id
            location = blob.storage_location

            # Drop any stale ``MailboxBlob`` rows for this blob.
            # ``is_referenced`` already excluded the active ones
            # (``expires_at > now()``), so anything still attached
            # here is past its TTL. We clear them inline because
            # ``MailboxBlob.blob`` is PROTECT — the subsequent
            # ``blob.delete()`` would otherwise raise ProtectedError.
            MailboxBlob.objects.filter(blob_id=blob_uuid).delete()

            blob.delete()

            if (
                location == BlobStorageLocationChoices.OBJECT_STORAGE
                and service.enabled
            ):
                # Defer the S3 cleanup to commit. Doing it inline
                # would orphan the bucket object on a commit-time
                # failure (deadlock retry, network blip, etc.):
                # the row would roll back to alive while the S3
                # bytes are gone — readers would then 404 on
                # legitimate access. ``delete_if_orphaned`` re-checks
                # the cohort count itself so a sibling row inserted
                # between commit and on_commit firing keeps the
                # bytes safe.
                service.defer_delete_if_orphaned(sha, key_id)
        return "deleted"
    except Exception:
        logger.exception("GC failed for blob %s", blob_id_str)
        return "error"


@celery_app.task
def gc_orphan_blobs_task(mode: str = "fast") -> Dict[str, Any]:
    """Periodic: GC blobs whose last reference was deleted.

    Modes:

    - ``"fast"`` (default, hooked to celery beat) — drain ids from the
      Redis candidate set and process each. Catches the common case
      where a Message / Attachment / MessageTemplate post_delete has
      pushed the blob_id.
    - ``"full"`` — walk every Blob row. Use as a periodic safety-net
      sweep (weekly cron via ``manage.py shell`` or a separate beat
      entry) to catch anything dropped by a Redis outage or a missing
      signal.

    Both modes:

    - Skip blobs with an active upload reservation (JMAP 2-step flow).
    - Re-check the reference graph inside a per-sha advisory lock to
      avoid racing the offload / re-store / dedup paths.
    - Do S3 cleanup inline (no per-blob celery fan-out) when the
      deleted row was at OBJECT_STORAGE.
    """
    if mode == "fast":
        candidate_iter: Iterable[str] = _drain_candidates(_GC_FAST_BATCH_SIZE)
    elif mode == "full":
        candidate_iter = _all_blob_ids_iterator()
    else:
        raise ValueError(f"unknown mode {mode!r} (use 'fast' or 'full')")

    service = TieredStorageService()
    deadline = monotonic() + _GC_MAX_RUN_SECONDS

    counts = {
        "mode": mode,
        "deleted": 0,
        "skipped_reserved": 0,
        "skipped_referenced": 0,
        "not_found": 0,
        "errors": 0,
        "stop_reason": "exhausted",
    }

    for blob_id_str in candidate_iter:
        if monotonic() >= deadline:
            counts["stop_reason"] = "deadline"
            break
        result = _gc_one_blob(blob_id_str, service)
        if result == "deleted":
            counts["deleted"] += 1
        elif result == "skipped_reserved":
            counts["skipped_reserved"] += 1
        elif result == "skipped_referenced":
            counts["skipped_referenced"] += 1
        elif result == "not_found":
            counts["not_found"] += 1
        else:
            counts["errors"] += 1

    logger.info(
        "gc_orphan_blobs_task[%s]: deleted=%d skipped_reserved=%d "
        "skipped_referenced=%d not_found=%d errors=%d stop=%s",
        counts["mode"],
        counts["deleted"],
        counts["skipped_reserved"],
        counts["skipped_referenced"],
        counts["not_found"],
        counts["errors"],
        counts["stop_reason"],
    )
    return counts

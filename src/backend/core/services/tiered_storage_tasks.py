"""
Tiered storage background tasks for blob offloading.

The periodic offload task walks the eligible queryset and processes
blobs sequentially within a single task invocation — no per-blob
fan-out, no broker amplification. Runs are bounded by a wall-clock
budget so the task always returns before it could hit its time
limit; whatever isn't done this tick gets picked up next tick.
Per-blob failures stay local (logged + skipped); the surrounding loop
keeps going.
"""

import logging
from datetime import timedelta
from time import monotonic
from typing import Any, Dict

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now

from botocore.exceptions import BotoCoreError, ClientError

from core.enums import BlobStorageLocationChoices
from core.models import Blob
from core.services.tiered_storage import TieredStorageService, sha256_advisory_lock
from core.task_utils import cron_task, register_task

logger = logging.getLogger(__name__)

# Transient exceptions worth recording but not crashing the loop on.
# OSError + BotoCoreError cover connection-level errors (timeouts,
# DNS, broken pipes). ClientError 5xx (S3 SlowDown, ServiceUnavailable,
# InternalError, etc.) is also transient and self-resolves. ClientError
# 4xx is persistent (NoSuchBucket, AccessDenied) and stays loud as a
# hard error — see ``_is_transient_storage_error`` for the split.
_TRANSIENT_EXCEPTIONS = (OSError, BotoCoreError)


def _is_transient_storage_error(exc: BaseException) -> bool:
    """Return True if ``exc`` should be classified as transient.

    ClientError is split by HTTP status: 5xx is the AWS / MinIO server
    saying "try again later" and is worth a retry next tick; 4xx
    (config / auth / missing bucket) is persistent and we want it loud
    in logs so an operator notices.
    """
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True
    if isinstance(exc, ClientError):
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return status >= 500
    return False


# Wall-clock budget per tick. The schedule is hourly and we cap at 55
# minutes so the task always returns before the next tick could overlap.
# Whatever isn't done this tick is picked up next tick.
_MAX_RUN_SECONDS = 55 * 60


@cron_task(crontab="5 * * * *")
# The in-task budget is the real bound; the time limit sits just past it so a
# run that stops itself cleanly is never mistaken for one that hung.
@register_task(queue="blobs", time_limit=_MAX_RUN_SECONDS + 300)
def offload_blobs_task(dry_run: bool = False) -> Dict[str, Any]:
    """Periodic task: offload eligible blobs to object storage.

    All work happens inside this single task — no per-blob
    fan-out. The loop processes blobs one at a time and stops when
    either the 55-minute wall-clock budget runs out or the queryset
    is exhausted. Per-blob errors (transient or permanent) are logged
    and the loop continues; the affected blob stays POSTGRES and gets
    reconsidered next tick.

    ``dry_run`` (default False): when True, identify the candidates that
    a real run would attempt (same queryset: POSTGRES location, age >=
    MESSAGES_BLOBS_OFFLOAD_DELAY, size >= MESSAGES_BLOBS_OFFLOAD_MIN_SIZE)
    and log one INFO line per candidate, but skip the upload, the row
    update, and the per-sha advisory lock. Neither
    ``MESSAGES_BLOBS_OFFLOAD_ENABLED`` nor ``service.enabled`` gates
    apply to dry-run so an operator can preview before configuring the
    bucket or flipping the master switch. The return counts use
    ``would_offload`` instead of ``success``; ``failed`` / ``skipped``
    stay at zero (a dry-run can't observe per-blob transient errors
    or lock contention).
    """
    if not dry_run:
        if not settings.MESSAGES_BLOBS_OFFLOAD_ENABLED:
            return {"status": "disabled", "processed": 0}

        service = TieredStorageService()
        if not service.enabled:
            return {"status": "disabled", "processed": 0}
    else:
        service = None  # not used in the dry-run loop

    cutoff_date = now() - timedelta(seconds=settings.MESSAGES_BLOBS_OFFLOAD_DELAY)
    deadline = monotonic() + _MAX_RUN_SECONDS

    queryset = Blob.objects.filter(
        storage_location=BlobStorageLocationChoices.POSTGRES,
        created_at__lt=cutoff_date,
        size__gte=settings.MESSAGES_BLOBS_OFFLOAD_MIN_SIZE,
    ).order_by("created_at")

    success = failed = skipped = would_offload = 0
    bytes_plain = bytes_stored = 0
    stop_reason = "exhausted"

    if dry_run:
        iter_qs = queryset.values(
            "id", "size", "size_compressed", "content_type", "created_at"
        ).iterator(chunk_size=200)
        for row in iter_qs:
            if monotonic() >= deadline:
                stop_reason = "deadline"
                break
            would_offload += 1
            bytes_plain += row["size"] or 0
            bytes_stored += row["size_compressed"] or 0
            logger.info(
                "offload[dry_run] would offload blob id=%s size=%s stored=%s "
                "content_type=%s created_at=%s",
                row["id"],
                row["size"],
                row["size_compressed"],
                row["content_type"],
                row["created_at"].isoformat(),
            )
    else:
        for blob_id in queryset.values_list("id", flat=True).iterator(chunk_size=200):
            if monotonic() >= deadline:
                stop_reason = "deadline"
                break

            result = offload_one_blob(str(blob_id), service)
            status = result.get("status")
            if status == "success":
                success += 1
            elif status in (
                "already_offloaded",
                "no_content",
                "not_found",
                "lock_held",
            ):
                skipped += 1
            else:
                failed += 1

    logger.info(
        "offload_blobs_task[%s]: success=%d would_offload=%d failed=%d "
        "skipped=%d stop=%s",
        "dry_run" if dry_run else "real",
        success,
        would_offload,
        failed,
        skipped,
        stop_reason,
    )
    result: Dict[str, Any] = {
        "status": "success",
        "dry_run": dry_run,
        "processed": success + failed + skipped + would_offload,
        "success": success,
        "would_offload": would_offload,
        "failed": failed,
        "skipped": skipped,
        "stop_reason": stop_reason,
    }
    if dry_run:
        result["bytes_plain"] = bytes_plain
        result["bytes_stored"] = bytes_stored
    return result


def offload_one_blob(blob_id: str, service: TieredStorageService) -> Dict[str, Any]:
    """Offload a single blob to object storage atomically.

    Acquires a per-sha256 advisory lock so concurrent cleanup, dedup,
    or re-encrypt of the same content cohort cannot interleave. If the
    lock is held elsewhere, the call returns ``status=lock_held`` and
    the caller moves on — the next tick will retry. Transient and
    permanent failures both return a status; nothing is raised.
    """
    if not service.enabled:
        return {"status": "disabled", "blob_id": blob_id}

    # sha256 is immutable, so we can safely look it up before taking the lock.
    try:
        sha256 = bytes(Blob.objects.values_list("sha256", flat=True).get(id=blob_id))
    except Blob.DoesNotExist:
        return {"status": "not_found", "blob_id": blob_id}

    try:
        with transaction.atomic(), sha256_advisory_lock(sha256, blocking=False) as got:
            if not got:
                # Another worker holds the per-sha lock (cleanup, re-encrypt,
                # etc.). Skip; we'll come back next tick.
                return {"status": "lock_held", "blob_id": blob_id}

            try:
                blob = Blob.objects.select_for_update().get(id=blob_id)
            except Blob.DoesNotExist:
                return {"status": "not_found", "blob_id": blob_id}

            if blob.storage_location != BlobStorageLocationChoices.POSTGRES:
                return {"status": "already_offloaded", "blob_id": blob_id}

            if blob.raw_content is None:
                logger.warning("Blob %s has no raw_content to offload", blob_id)
                return {"status": "no_content", "blob_id": blob_id}

            key_id, compression = service.upload_blob(blob)

            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.encryption_key_id = key_id
            # Adopt the existing object's compression on dedup hits;
            # for fresh uploads this is a no-op (matches blob.compression).
            blob.compression = compression
            blob.raw_content = None
            blob.save(
                update_fields=[
                    "storage_location",
                    "encryption_key_id",
                    "compression",
                    "raw_content",
                ]
            )

            logger.info(
                "Offloaded blob %s to object storage (key_id=%d)", blob_id, key_id
            )
            return {"status": "success", "blob_id": blob_id, "key_id": key_id}

    except Exception as e:  # pylint: disable=broad-except
        if _is_transient_storage_error(e):
            logger.warning("Transient error offloading blob %s: %s", blob_id, e)
            return {"status": "transient_error", "blob_id": blob_id, "error": str(e)}
        logger.exception("Failed to offload blob %s", blob_id)
        return {"status": "error", "blob_id": blob_id, "error": str(e)}

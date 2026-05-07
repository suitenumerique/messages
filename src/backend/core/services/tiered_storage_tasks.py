"""
Tiered storage Celery tasks for blob offloading.

The periodic offload task walks the eligible queryset and processes
blobs sequentially within a single task invocation — no per-blob
fan-out, no broker amplification. Runs are bounded by a wall-clock
budget so the task always returns to celery before it could be
soft-killed; whatever isn't done this tick gets picked up next tick.
Per-blob failures stay local (logged + skipped); the surrounding loop
keeps going.
"""

from datetime import timedelta
from time import monotonic
from typing import Any, Dict

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now

from botocore.exceptions import BotoCoreError, ClientError
from celery.utils.log import get_task_logger

from core.enums import BlobStorageLocationChoices
from core.models import Blob
from core.services.tiered_storage import TieredStorageService, sha256_advisory_lock

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)

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


# Wall-clock budget per beat tick. The schedule is hourly (3600s) and
# we cap at 55 minutes so the task always returns to celery before the
# next beat tick could overlap. Whatever isn't done this tick is picked
# up next tick.
_MAX_RUN_SECONDS = 55 * 60


@celery_app.task
def offload_blobs_task() -> Dict[str, Any]:
    """Periodic task: offload eligible blobs to object storage.

    All work happens inside this single task — no per-blob celery
    fan-out. The loop processes blobs one at a time and stops when
    either the 55-minute wall-clock budget runs out or the queryset
    is exhausted. Per-blob errors (transient or permanent) are logged
    and the loop continues; the affected blob stays POSTGRES and gets
    reconsidered next tick.
    """
    if not settings.MESSAGES_BLOBS_OFFLOAD_ENABLED:
        return {"status": "disabled", "processed": 0}

    service = TieredStorageService()
    if not service.enabled:
        return {"status": "disabled", "processed": 0}

    cutoff_date = now() - timedelta(seconds=settings.MESSAGES_BLOBS_OFFLOAD_DELAY)
    deadline = monotonic() + _MAX_RUN_SECONDS

    queryset = (
        Blob.objects.filter(
            storage_location=BlobStorageLocationChoices.POSTGRES,
            created_at__lt=cutoff_date,
            size__gte=settings.MESSAGES_BLOBS_OFFLOAD_MIN_SIZE,
        )
        .order_by("created_at")
        .values_list("id", flat=True)
    )

    success = failed = skipped = 0
    stop_reason = "exhausted"
    for blob_id in queryset.iterator(chunk_size=200):
        if monotonic() >= deadline:
            stop_reason = "deadline"
            break

        result = offload_one_blob(str(blob_id), service)
        status = result.get("status")
        if status == "success":
            success += 1
        elif status in ("already_offloaded", "no_content", "not_found", "lock_held"):
            skipped += 1
        else:
            failed += 1

    logger.info(
        "offload_blobs_task: success=%d failed=%d skipped=%d stop=%s",
        success,
        failed,
        skipped,
        stop_reason,
    )
    return {
        "status": "success",
        "processed": success + failed + skipped,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "stop_reason": stop_reason,
    }


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

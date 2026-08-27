"""Shared, format-agnostic helpers used by the per-source import runners.

Every ``run_<format>()`` runner (in ``mbox``/``eml``/``pst``/``imap``) executes
one resumable pass with the same three primitives from here — deliver a message
under the import channel, beat the heartbeat + poll for cancel, and reach the
imports bucket — plus the ``FLUSH_EVERY`` cadence and the ``TransientImportError``
signal. Kept in a leaf module (it imports ``channel``/``models``/``mda`` but no
format module and not ``runner``) so the runners can import it without a cycle.
"""

# pylint: disable=broad-exception-caught
from django.conf import settings
from django.core.files.storage import storages

from celery.utils.log import get_task_logger
from jmap_email import first_address_email, parse_email

from core import models
from core.mda.addresses import normalize_address
from core.mda.inbound import deliver_inbound_message
from core.utils import ThreadReindexDeferrer, ThreadStatsUpdateDeferrer

from .channel import (
    ImportCancelled,
    heartbeat,
    is_cancel_requested,
    mark_started,
    record_progress,
    renew_run_lock,
)

logger = get_task_logger(__name__)

# Flush the Redis watermark + counts (and beat the heartbeat) every N messages.
# Small enough to lose little work on a crash, large enough to keep Redis writes
# off the per-message path.
FLUSH_EVERY = 25

# Failure reason categories tallied per run (a ``{reason: count}`` map persisted
# in the run state) so the imports list can explain *why* messages were skipped
# — a bare "N failed" is un-actionable. Kept coarse on purpose: the user only
# needs to know whether it's a size cap, a corrupt message, or a delivery error.
FAILURE_OVERSIZED = "oversized"
FAILURE_UNPARSEABLE = "unparseable"
FAILURE_UNDELIVERABLE = "undeliverable"
FAILURE_ERROR = "error"


def bump_reason(reasons: dict[str, int] | None, reason: str) -> None:
    """Increment ``reasons[reason]`` when a caller is collecting a breakdown."""
    if reasons is not None:
        reasons[reason] = reasons.get(reason, 0) + 1


class TransientImportError(Exception):
    """A recoverable error (e.g. an IMAP fetch that timed out even after
    retries). The run is left ``is_active`` + resumable rather than marked
    terminally failed, so the scheduler re-dispatches it and it resumes from the
    persisted watermark (no silent message loss)."""


def error_text(exc: BaseException) -> str:
    """A clean, user-facing error string for the import's ``error`` field.

    Several libraries (imaplib especially) raise exceptions whose arg is
    ``bytes`` — ``str(exc)`` then yields an ugly ``b'...'`` repr. Decode it so
    the message the UI shows a user is readable.
    """
    if getattr(exc, "args", None) and isinstance(exc.args[0], (bytes, bytearray)):
        return exc.args[0].decode("utf-8", errors="replace").strip()
    return str(exc).strip()


def imports_storage():
    """The message-imports bucket storage + its underlying boto3 S3 client."""
    storage = storages["message-imports"]
    return storage, storage.connection.meta.client


def beat(channel) -> None:
    """Per-message liveness + cooperative-cancel poll (all cheap).

    Beating the heartbeat and renewing the lock on *every* message (not only on
    the batched watermark flush) keeps a live-but-slow run from looking stalled:
    the scheduler's freshness check then reliably sees it as alive and won't
    force a second concurrent runner onto the same channel — even if a single
    message takes tens of seconds. ``heartbeat`` is throttled (its DB write is a
    no-op most calls) and lock renewal is one Redis SET, so this is inexpensive.

    It is also the cancel-poll point: an in-flight run unwinds via
    ``ImportCancelled`` promptly instead of running to completion and
    overwriting the cancelled status with ``completed``.
    """
    heartbeat(channel)
    renew_run_lock(channel.id)
    if is_cancel_requested(channel.id):
        raise ImportCancelled()


def deliver(
    raw_bytes: bytes,
    recipient: models.Mailbox,
    channel: models.Channel,
    *,
    imap_labels: list[str] | None = None,
    imap_flags: list[str] | None = None,
    is_sender: bool | None = None,
    reasons: dict[str, int] | None = None,
) -> bool:
    """Parse + deliver one message under the import channel. Returns success.

    When ``reasons`` is passed, each way a message can fail bumps the matching
    category so the run can report a breakdown (see ``run_plan`` / the imap
    runner). Kept as an out-param rather than a richer return so the many
    ``if deliver(...)`` call sites stay a plain truthiness check.
    """
    if len(raw_bytes) > settings.MAX_INCOMING_EMAIL_SIZE:
        logger.warning("import: skipping oversized message (%d bytes)", len(raw_bytes))
        bump_reason(reasons, FAILURE_OVERSIZED)
        return False
    parsed_email = parse_email(raw_bytes)
    if parsed_email is None:
        logger.warning(
            "import: skipping unparseable message (%d bytes)", len(raw_bytes)
        )
        bump_reason(reasons, FAILURE_UNPARSEABLE)
        return False

    recipient_email = str(recipient)
    if is_sender is None:
        # Treat as sent mail when From matches the destination mailbox, so a
        # user's own sent mail skips the inbox.
        sender_email = first_address_email(parsed_email.get("from")) or ""
        is_sender = bool(sender_email) and normalize_address(
            sender_email
        ) == normalize_address(recipient_email)

    delivered = bool(
        deliver_inbound_message(
            recipient_email,
            parsed_email,
            raw_bytes,
            is_import=True,
            is_import_sender=is_sender,
            imap_labels=imap_labels,
            imap_flags=imap_flags,
            channel=channel,
        )
    )
    if not delivered:
        bump_reason(reasons, FAILURE_UNDELIVERABLE)
    return delivered


def run_plan(channel, state, plan, deliver_item) -> tuple[int, int, int]:
    """Deliver each item of a positional plan, resuming from ``state``'s cursor.

    ``plan`` is a materialised sequence (so ``len(plan)`` is the authoritative
    total); ``deliver_item(item)`` delivers one item and returns whether it
    succeeded. Beats the heartbeat (and polls for cancel) before each item,
    tallies success/failure, and checkpoints the cursor + counts every
    ``FLUSH_EVERY`` messages. The file runners (mbox/eml/pst) share this pass and
    differ only in how they build ``plan`` and deliver a single item.
    """
    total = len(plan)
    # Resume watermark for file sources: a positional index into ``plan``. Items
    # [0, cursor) are already delivered, so the loop restarts at ``cursor`` (0 on
    # a fresh run or after a Redis eviction — a from-scratch replay dedup makes
    # safe).
    cursor = state.get("cursor", 0)
    success, failure = state.get("success", 0), state.get("failure", 0)
    # Resume the reason breakdown alongside the counts so a resumed run keeps a
    # coherent tally (``deliver_item`` bumps it via ``deliver(reasons=...)``).
    reasons: dict[str, int] = dict(state.get("failure_reasons") or {})
    mark_started(channel.id, total=total)

    with ThreadReindexDeferrer.defer(), ThreadStatsUpdateDeferrer.defer():
        for index in range(cursor, total):
            beat(channel)
            try:
                if deliver_item(plan[index], reasons):
                    success += 1
                else:
                    failure += 1
            except Exception:
                logger.exception("import: error on message %d", index)
                failure += 1
                bump_reason(reasons, FAILURE_ERROR)
            if (index + 1) % FLUSH_EVERY == 0:
                record_progress(
                    channel.id,
                    success=success,
                    failure=failure,
                    cursor=index + 1,
                    total=total,
                    failure_reasons=reasons,
                )
    # Final checkpoint: the last partial batch (< FLUSH_EVERY) never hit the loop
    # flush, so persist the closing counts + breakdown before mark_finished reads
    # them back.
    record_progress(
        channel.id,
        success=success,
        failure=failure,
        cursor=total,
        total=total,
        failure_reasons=reasons,
    )
    return success, failure, total

"""Message delivery and processing tasks.

Per-message processing is a pipeline of ``Step``s — see
``inbound_pipeline.py``. This module is the Celery task wrapper:
acquire a Redis lock, parse the bytes, build the context + pipeline,
iterate, and turn the final ``Decision`` into a task return value.
"""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught

from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from celery.utils.log import get_task_logger
from jmap_email import JmapEmail, first_address_email, parse_email

from core import models
from core.mda.inbound_create import _create_message_from_inbound
from core.mda.inbound_pipeline import (
    RETRY_MAX_AGE,
    Decision,
    InboundContext,
    apply_labels_to_thread,
    apply_pending_assigns,
    apply_pending_drafts,
    apply_pending_events,
    apply_thread_access_flags,
    build_inbound_pipeline,
    run_inbound_pipeline,
)

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


def _is_selfcheck(parsed_email: JmapEmail, recipient_email: str) -> bool:
    """Strict envelope match for the configured self-probe.

    The self-probe is an internal liveness check sent from
    ``MESSAGES_SELFCHECK_FROM`` to ``MESSAGES_SELFCHECK_TO``. We short-
    circuit spam checking for it so the probe is never junked, but it
    still flows through the rest of the pipeline (inbound auth, after-
    spam webhooks, message creation).
    """
    selfcheck_from = (settings.MESSAGES_SELFCHECK_FROM or "").strip().lower()
    selfcheck_to = (settings.MESSAGES_SELFCHECK_TO or "").strip().lower()
    if not selfcheck_from or not selfcheck_to:
        return False

    from_email = first_address_email(parsed_email.get("from")).strip().lower()
    if from_email != selfcheck_from:
        return False
    return (recipient_email or "").strip().lower() == selfcheck_to


def _safe_finalize(label, inbound_message_id, gate, fn):
    """Run one finalize step under an isolated try/except.

    ``gate`` short-circuits the call when the input collection is
    empty/false — same semantics as the inline ``if ctx.labels:``
    guards, just lifted out. Exceptions are logged but never
    propagated: the message has already landed; failing the whole
    task here would only confuse operators."""
    if not gate:
        return
    try:
        fn()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Finalize step %r failed on inbound message %s: %s",
            label,
            inbound_message_id,
            exc,
        )


def _handle_retry(
    inbound_message: models.InboundMessage, step_name: Optional[str]
) -> Dict[str, Any]:
    """Translate a RETRY decision into the task return value.

    The InboundMessage row is kept in place unless it's older than
    ``RETRY_MAX_AGE`` — the 5-min sweep
    (``process_inbound_messages_queue_task``) re-fires the task on the
    next cycle. Past the budget we drop and log loudly so a
    permanently-broken receiver can't pin a row forever.
    """
    age = timezone.now() - inbound_message.created_at
    if age > RETRY_MAX_AGE:
        logger.error(
            "Inbound message %s exceeded retry budget (%s old) — dropping at step=%s",
            inbound_message.id,
            age,
            step_name,
        )
        inbound_message.delete()
        return {
            "success": False,
            "inbound_message_id": str(inbound_message.id),
            "error": "retry_exhausted",
            "step": step_name,
        }
    logger.info(
        "Inbound message %s held for retry at step=%s (age=%s)",
        inbound_message.id,
        step_name,
        age,
    )
    return {
        "success": False,
        "inbound_message_id": str(inbound_message.id),
        "error": "retry",
        "step": step_name,
    }


@celery_app.task(bind=True)
def process_inbound_message_task(self, inbound_message_id: str):
    """Process an inbound message: run the pipeline, persist the result.

    Returns ``{"success": ...}`` so the 5-min retry sweep can tell which
    messages still need work. On DROP, the ``InboundMessage`` row is
    deleted (we're done with it) and the task reports success.
    """
    # Redis lock keyed on the message id prevents two workers from
    # racing on the same row. Auto-expires after 5 min so a hung worker
    # doesn't block the next sweep.
    lock_key = f"process_inbound_message_lock:{inbound_message_id}"
    if not cache.add(lock_key, "locked", 300):
        logger.warning(
            "InboundMessage %s is already being processed — skipping",
            inbound_message_id,
        )
        return {"success": False, "error": "Message already being processed"}

    inbound_message: Optional[models.InboundMessage] = None
    try:
        try:
            inbound_message = models.InboundMessage.objects.get(id=inbound_message_id)
        except models.InboundMessage.DoesNotExist:
            error_msg = f"InboundMessage with ID '{inbound_message_id}' does not exist"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        raw_data_bytes = bytes(inbound_message.raw_data)
        parsed_email = parse_email(raw_data_bytes)
        if parsed_email is None:
            error_msg = "Failed to parse email message"
            logger.error(error_msg)
            inbound_message.error_message = error_msg
            inbound_message.save(update_fields=["error_message"])
            return {"success": False, "error": error_msg}

        mailbox = inbound_message.mailbox
        recipient_email = str(mailbox)
        ctx = InboundContext(
            mailbox=mailbox,
            inbound_message=inbound_message,
            recipient_email=recipient_email,
            raw_data=raw_data_bytes,
            parsed_email=parsed_email,
            spam_config=mailbox.domain.get_spam_config(),
        )
        if _is_selfcheck(parsed_email, recipient_email):
            # System self-probe: short-circuit the spam check before
            # the pipeline runs. The hardcoded-rules + rspamd steps
            # both no-op when ctx.is_spam is already set.
            ctx.is_spam = False
            logger.debug(
                "Selfcheck probe — pre-setting is_spam=False for %s",
                inbound_message_id,
            )

        decision, aborted_by = run_inbound_pipeline(build_inbound_pipeline(ctx), ctx)

        if decision == Decision.DROP:
            logger.info(
                "Inbound message %s dropped by step=%s",
                inbound_message_id,
                aborted_by,
            )
            inbound_message.delete()
            return {
                "success": True,
                "inbound_message_id": str(inbound_message_id),
                "dropped_by": aborted_by,
            }
        if decision == Decision.RETRY:
            return _handle_retry(inbound_message, aborted_by)

        inbound_msg = _create_message_from_inbound(
            recipient_email=ctx.recipient_email,
            parsed_email=ctx.parsed_email,
            raw_data=ctx.raw_data,
            mailbox=mailbox,
            channel=inbound_message.channel,
            is_spam=bool(ctx.is_spam),
            is_trashed=ctx.mark_trashed,
            is_archived=ctx.mark_archived,
        )

        if inbound_msg:
            inbound_message.delete()

            if isinstance(inbound_msg, models.Message):
                # Each finalize step is isolated — a failure in one
                # (DB hiccup, race with admin deletion) must not skip
                # the others. The message has landed; best effort.
                _safe_finalize(
                    "labels",
                    inbound_message_id,
                    ctx.labels,
                    lambda: apply_labels_to_thread(
                        inbound_msg.thread, mailbox, ctx.labels
                    ),
                )
                _safe_finalize(
                    "assigns",
                    inbound_message_id,
                    ctx.pending_assigns,
                    lambda: apply_pending_assigns(
                        inbound_msg.thread, ctx.pending_assigns
                    ),
                )
                _safe_finalize(
                    "events",
                    inbound_message_id,
                    ctx.pending_events,
                    lambda: apply_pending_events(
                        inbound_msg.thread, ctx.pending_events
                    ),
                )
                _safe_finalize(
                    "drafts",
                    inbound_message_id,
                    ctx.pending_drafts,
                    lambda: apply_pending_drafts(
                        inbound_msg, mailbox, ctx.pending_drafts
                    ),
                )
                _safe_finalize(
                    "flags",
                    inbound_message_id,
                    ctx.mark_starred or ctx.mark_read,
                    lambda: apply_thread_access_flags(
                        inbound_msg.thread,
                        mailbox,
                        mark_starred=ctx.mark_starred,
                        mark_read=ctx.mark_read,
                    ),
                )

            if isinstance(inbound_msg, models.Message) and not ctx.skip_autoreply:
                from core.mda.autoreply import (  # pylint: disable=import-outside-toplevel
                    try_send_autoreply,
                )

                # ``try_send_autoreply`` already suppresses for spam.
                # The ``skip_autoreply`` flag wraps the same gate from
                # the outside so a non-spam message can also opt out
                # (e.g. when the webhook itself replies).
                try_send_autoreply(
                    mailbox, ctx.parsed_email, inbound_msg, is_spam=bool(ctx.is_spam)
                )

            logger.info(
                "Successfully processed inbound message %s (is_spam=%s)",
                inbound_message_id,
                ctx.is_spam,
            )

            return {
                "success": True,
                "inbound_message_id": str(inbound_message_id),
                "is_spam": ctx.is_spam,
            }

        error_msg = "Failed to create message from inbound message"
        inbound_message.error_message = error_msg
        inbound_message.save(update_fields=["error_message"])
        # Keep the message for retry
        return {"success": False, "error": error_msg}

    except Exception as e:
        logger.exception(
            "Error processing inbound message %s: %s", inbound_message_id, e
        )
        if inbound_message:
            inbound_message.error_message = str(e)
            inbound_message.save(update_fields=["error_message"])
        return {"success": False, "error": str(e)}
    finally:
        # Always release the lock
        cache.delete(lock_key)


@celery_app.task(bind=True)
def process_inbound_messages_queue_task(self, batch_size: int = 10):
    """Retry processing of inbound messages that are older than 5 minutes.

    This task only handles retries for messages that may have failed or gotten stuck.
    Regular messages are processed immediately when created via process_inbound_message_task.delay().

    Args:
        batch_size: Number of messages to process in this batch

    Returns:
        dict: A dictionary with processing results
    """
    # Only retry messages older than 5 minutes
    retry_threshold = timezone.now() - timezone.timedelta(minutes=5)
    old_messages = models.InboundMessage.objects.filter(
        created_at__lt=retry_threshold
    ).order_by("created_at")[:batch_size]

    total = len(old_messages)
    if total == 0:
        return {
            "success": True,
            "processed": 0,
            "total": 0,
        }

    processed = 0
    errors = 0

    for inbound_message in old_messages:
        try:
            # Trigger async task for each old message (retry)
            process_inbound_message_task.delay(str(inbound_message.id))
            processed += 1
        except Exception as e:
            logger.exception(
                "Error queuing inbound message %s for retry: %s",
                inbound_message.id,
                e,
            )
            errors += 1

    return {
        "success": True,
        "processed": processed,
        "errors": errors,
        "total": total,
    }

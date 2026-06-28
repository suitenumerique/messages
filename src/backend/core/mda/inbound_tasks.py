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
from core.mda.dispatch_webhooks import dispatch_recorded_webhooks
from core.mda.inbound_create import _create_message_from_inbound
from core.mda.inbound_pipeline import (
    QUARANTINE_AFTER,
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

    The InboundMessage row is kept in place — the 5-min sweep
    (``process_inbound_messages_queue_task``) re-fires the task on the
    next cycle. We never drop here: a persistently-failing blocking
    webhook is bounded instead by ``QUARANTINE_AFTER`` (the message is
    then delivered flagged, see ``_stamp_processing_failed``), and the
    webhook step is the only thing that produces a RETRY.
    """
    age = timezone.now() - inbound_message.created_at
    logger.info(
        "Inbound message %s held for retry at step=%s (age=%s)",
        inbound_message.id,
        step_name,
        age,
    )
    # Record why the row is parked so the queue is diagnosable straight
    # from the admin / DB without grepping logs — important now that a
    # webhook failure holds the message here instead of dropping it.
    inbound_message.error_message = (
        f"Held for retry at step={step_name}" if step_name else "Held for retry"
    )
    inbound_message.save(update_fields=["error_message"])
    return {
        "success": False,
        "inbound_message_id": str(inbound_message.id),
        "error": "retry",
        "step": step_name,
    }


def _retry_or_abandon(
    inbound_message: models.InboundMessage, reason: str
) -> Dict[str, Any]:
    """Bounded handling for a message that failed to be created/processed.

    Within ``QUARANTINE_AFTER`` the row is kept so the 5-min sweep retries
    it (a transient DB error or constraint hiccup clears on its own). Past
    the window the attempt is abandoned and the row deleted: otherwise a
    poison message (one that parses but can never be created) loops
    forever, re-running the whole pipeline — and re-firing every user
    webhook — on each sweep.
    """
    age = timezone.now() - inbound_message.created_at
    if age <= QUARANTINE_AFTER:
        inbound_message.error_message = reason
        inbound_message.save(update_fields=["error_message"])
        return {
            "success": False,
            "inbound_message_id": str(inbound_message.id),
            "error": "retry",
            "reason": reason,
        }
    logger.error(
        "Inbound message %s abandoned after persistent failure (age=%s): %s",
        inbound_message.id,
        age,
        reason,
    )
    inbound_message.delete()
    return {
        "success": False,
        "inbound_message_id": str(inbound_message.id),
        "error": "abandoned",
        "reason": reason,
    }


def _stamp_processing_failed(ctx: InboundContext) -> None:
    """Prepend the ``X-StMsg-Processing-Failed`` marker to the message.

    Mirrors the ``X-StMsg-Sender-Auth`` prepend in the pipeline: the
    header rides in the stored MIME, ``Message.get_stmsg_headers()``
    surfaces it as ``processing-failed``, and the frontend renders a
    warning banner. Deliberately generic — any processing step that
    fails persistently (a blocking webhook, rspamd, …) lands here.
    Sender-supplied ``X-StMsg-*`` headers are stripped at ingest, so this
    namespace is ours alone — the flag can't be forged.
    """
    prepended = b"X-StMsg-Processing-Failed: true\r\n" + ctx.raw_data
    reparsed = parse_email(prepended)
    if reparsed is not None:
        ctx.parsed_email = reparsed
        ctx.raw_data = prepended
    else:
        # Keep raw_data / parsed_email in lockstep — drop the marker
        # rather than corrupt the blob (same fallback as Sender-Auth).
        logger.warning("Failed to re-parse after prepending X-StMsg-Processing-Failed")


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

        raw_data_bytes = inbound_message.get_raw_bytes()
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
        if inbound_message.is_internal or _is_selfcheck(parsed_email, recipient_email):
            # Internal mailbox-to-mailbox mail is trusted, and the system
            # self-probe must never be junked: short-circuit the spam
            # check before the pipeline runs. The hardcoded-rules + rspamd
            # steps both no-op when ctx.is_spam is already set, but the
            # user-webhook steps still fire — so internal mail looks
            # identical to external mail to a webhook consumer.
            ctx.is_spam = False
            logger.debug(
                "Skipping spam check (internal=%s) for %s",
                inbound_message.is_internal,
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
        quarantined = False
        if decision == Decision.RETRY:
            age = timezone.now() - inbound_message.created_at
            if age <= QUARANTINE_AFTER:
                return _handle_retry(inbound_message, aborted_by)
            # Past the quarantine window: a processing step (blocking
            # webhook, rspamd, …) has failed persistently. Stop holding —
            # deliver the message anyway so it's never lost, but stamp it
            # so the UI warns the recipient it bypassed a processing step,
            # and land it in the inbox (is_spam=False) so the warning is
            # actually seen rather than buried in the spam folder.
            logger.warning(
                "Inbound message %s quarantine-delivered after persistent "
                "failure at step=%s (age=%s)",
                inbound_message_id,
                aborted_by,
                age,
            )
            _stamp_processing_failed(ctx)
            quarantined = True
            # The message is being forced to the inbox, so it is no longer
            # treated as spam. Normalize ctx.is_spam so downstream consumers
            # (autoreply gate, task result) agree with where it actually lands.
            ctx.is_spam = False
            # ...but a quarantine delivery means a processing step never
            # completed: the forced is_spam=False is a placement decision,
            # not a real spam verdict, and a blocking step that wanted to
            # suppress the reply (or classify the sender as spam) never got
            # to run. Don't fire an autoreply to a sender we couldn't fully
            # vet — suppress it for quarantined messages.
            ctx.skip_autoreply = True

        inbound_msg = _create_message_from_inbound(
            recipient_email=ctx.recipient_email,
            parsed_email=ctx.parsed_email,
            raw_data=ctx.raw_data,
            mailbox=mailbox,
            channel=inbound_message.channel,
            is_spam=False if quarantined else bool(ctx.is_spam),
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
                _safe_finalize(
                    "webhooks",
                    inbound_message_id,
                    ctx.pending_webhooks,
                    lambda: dispatch_recorded_webhooks(
                        inbound_msg, mailbox, ctx.pending_webhooks
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
                #
                # Best-effort: the message has already landed (and the
                # InboundMessage row is already deleted). A send failure
                # here must not bubble to the outer ``except`` — that
                # would try to retry/abandon an already-deleted row.
                try:
                    try_send_autoreply(
                        mailbox,
                        ctx.parsed_email,
                        inbound_msg,
                        is_spam=bool(ctx.is_spam),
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception(
                        "Autoreply failed for inbound message %s", inbound_message_id
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

        # Creation failed (transient DB error, constraint, …). Hold for a
        # bounded retry rather than keeping the row forever.
        return _retry_or_abandon(
            inbound_message, "Failed to create message from inbound message"
        )

    except Exception as e:
        logger.exception(
            "Error processing inbound message %s: %s", inbound_message_id, e
        )
        if inbound_message:
            # Same bounded-retry policy as a failed creation: a persistent
            # error must not pin the row (and re-fire webhooks) forever.
            return _retry_or_abandon(inbound_message, str(e))
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

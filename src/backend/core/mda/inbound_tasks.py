"""Message delivery and processing tasks.

Per-message processing is a pipeline of ``Step``s — see
``inbound_pipeline.py``. This module is the Celery task wrapper:
acquire a Redis lock, parse the bytes, build the context + pipeline,
iterate, and turn the final ``Decision`` into a task return value.
"""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught

from datetime import timedelta
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from jmap_email import first_address_email, parse_email
from jmap_email.types import JmapEmail

from core import models
from core.mda.dispatch_webhooks import (
    dispatch_recorded_webhooks,
    load_cached_webhook_results,
    persist_cached_webhook_results,
)
from core.mda.inbound_create import (
    _create_message_from_inbound,
    _record_divergent_rcpt,
)
from core.mda.inbound_pipeline import (
    DEFERRAL_MAX_AGE,
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
from core.services.push import enqueue_push_notifications

from messages.celery_app import app as celery_app

logger = get_task_logger(__name__)


# Hard ceiling on one inbound task's wall-clock (Celery kills the task here).
# A deliberately non-configurable constant: it's an internal safety bound
# sized to the worst-case blocking-webhook budget (each up to 30s, fired for
# every matching channel across both pipeline phases), not an operator knob.
# The soft limit fires 60s earlier, raising ``SoftTimeLimitExceeded`` inside
# the task so it bails out gracefully (releases the lock, holds for retry)
# instead of being hard-killed mid-flight.
_INBOUND_TASK_TIME_LIMIT = 600  # seconds (10 min)
_INBOUND_TASK_SOFT_TIME_LIMIT = max(_INBOUND_TASK_TIME_LIMIT - 60, 1)
# The per-message lock must outlive the hard limit. On a clean (or soft-limit)
# exit the ``finally`` releases it immediately; on a hard-kill / worker OOM the
# lock is freed only by this TTL. Setting it just past the hard limit means a
# *live* task can never have its lock stolen (Celery kills the task before the
# lock expires), while a *dead* task's lock frees ~a minute later so the 5-min
# sweep can retry.
_INBOUND_TASK_LOCK_TTL = _INBOUND_TASK_TIME_LIMIT + 60

# How often a held row is re-attempted, as a function of how long it has
# already been failing: ``(minimum age, interval between attempts)``, in
# increasing age order. A row picks the last band whose minimum age it has
# reached.
#
# Both inputs are existing columns — ``created_at`` for the age, ``updated_at``
# for the previous attempt (every attempt stamps it) — so this needs no schema
# change. Outbound does the same thing with an explicit ``retry_count`` /
# ``retry_at`` pair on the recipient row (``RETRY_INTERVALS`` in
# ``outbound.py``); deriving the interval from age instead of counting attempts
# gives the same spacing without the columns.
#
# The point is that a dependency outage is not cheaper to survive by asking
# more often. A flat retry every 5 minutes turns one degraded webhook provider
# into ~600 pipeline runs per message over the deferral window, each re-POSTing
# to the same dead endpoint; the schedule below covers 48h in ~28 attempts.
_RETRY_BACKOFF = [
    (timedelta(0), timedelta(minutes=5)),
    (timedelta(minutes=30), timedelta(minutes=15)),
    (timedelta(hours=2), timedelta(hours=1)),
    (timedelta(hours=8), timedelta(hours=4)),
]


def _due_for_retry_q(now) -> Q:
    """Rows whose next attempt is due, per ``_RETRY_BACKOFF``.

    One OR'd band per entry, each bounded to its own age range so the bands
    are disjoint and a row matches exactly one. A band is satisfied when the
    row's last attempt (``updated_at``) is at least that band's interval old.

    A freshly-queued row has ``updated_at == created_at``, so the first band
    also supplies the "don't touch it for 5 minutes" grace the immediate
    ``.delay()`` dispatch needs to do its work unraced.
    """
    due = Q()
    for index, (min_age, interval) in enumerate(_RETRY_BACKOFF):
        band = Q(created_at__lte=now - min_age, updated_at__lte=now - interval)
        if index + 1 < len(_RETRY_BACKOFF):
            band &= Q(created_at__gt=now - _RETRY_BACKOFF[index + 1][0])
        due |= band
    return due


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
    guards, just lifted out. ALL exceptions (including a Celery
    ``SoftTimeLimitExceeded``) are logged and swallowed, never propagated:
    these run AFTER the message has landed and its queue row is deleted, so
    re-raising would make the task-level handler retry/abandon a row that no
    longer exists. A dropped finalize side effect is the acceptable cost."""
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
    next cycle. We never drop here: a persistently-failing processing step
    (a blocking webhook, or rspamd being unreachable) is bounded instead by
    ``DEFERRAL_MAX_AGE`` (the message is then delivered flagged, see
    ``_stamp_processing_failed``). The blocking webhook steps and the rspamd
    step are the producers of a RETRY.
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
    # Bump ``updated_at`` so the admin shows the latest retry activity. It is
    # ``auto_now`` but Django omits auto_now fields from the write unless
    # they're in ``update_fields`` — and a repeat retry may leave
    # ``error_message`` unchanged, so list it explicitly to touch the row.
    inbound_message.save(update_fields=["error_message", "updated_at"])
    return {
        "success": False,
        "inbound_message_id": str(inbound_message.id),
        "error": "retry",
        "step": step_name,
    }


def _retry_or_abandon(
    inbound_message: models.InboundMessage,
    reason: str,
    blocking_webhook_results: Optional[Dict[Any, Any]] = None,
) -> Dict[str, Any]:
    """Bounded handling for a message that failed to be created/processed.

    Within ``DEFERRAL_MAX_AGE`` the row is kept so the 5-min sweep retries
    it (a transient DB error or constraint hiccup clears on its own). Past
    the window the attempt is abandoned: ``abandoned_at`` is stamped so the
    sweep skips the row and stops re-running the whole pipeline — and
    re-firing every user webhook — on it, but the row is NOT deleted. The
    referenced ``blob`` is the only copy of the message, so deleting would
    silently lose mail; instead an operator
    can inspect and replay the row from the Django admin, and ``logger.error``
    raises a Sentry alert. ``error_message`` keeps the human-readable reason.

    ``blocking_webhook_results`` (when the failure happened AFTER the pipeline
    already ran the blocking webhooks) is persisted on the retry path so the
    next sweep replays those successes from cache instead of re-POSTing them.
    """
    age = timezone.now() - inbound_message.created_at
    if age <= DEFERRAL_MAX_AGE:
        if blocking_webhook_results:
            persist_cached_webhook_results(
                str(inbound_message.id), blocking_webhook_results
            )
        inbound_message.error_message = reason
        # See ``_handle_retry``: list ``updated_at`` so each retry bumps it
        # even when ``error_message`` is identical to the previous attempt.
        inbound_message.save(update_fields=["error_message", "updated_at"])
        return {
            "success": False,
            "inbound_message_id": str(inbound_message.id),
            "error": "retry",
            "reason": reason,
        }
    logger.error(
        "Inbound message %s abandoned after persistent failure (age=%s) — "
        "see its error_message field for details",
        inbound_message.id,
        age,
    )
    return _abandon(inbound_message, reason)


def _abandon(inbound_message: models.InboundMessage, reason: str) -> Dict[str, Any]:
    """Stamp a message terminally failed so the sweep stops retrying it.

    Keeps the row and its bytes — the ``blob`` is the only copy of the
    mail, so an operator can still inspect and replay it from the admin.

    Called directly rather than via ``_retry_or_abandon`` for
    deterministic failures, which no number of retries can clear.
    """
    inbound_message.error_message = reason
    inbound_message.abandoned_at = timezone.now()
    inbound_message.save(update_fields=["error_message", "abandoned_at", "updated_at"])
    return {
        "success": False,
        "inbound_message_id": str(inbound_message.id),
        "error": "abandoned",
        "reason": reason,
    }


def _stamp_processing_failed(ctx: InboundContext) -> None:
    """Record the ``processing`` failure marker in ``ctx.postmark``.

    Written structurally (not prepended to the bytes), so the ingest blob is
    reused untouched as ``Message.blob``. ``Message.get_stmsg_headers()``
    surfaces it as ``processing-failed`` and the frontend renders a warning
    banner. Deliberately generic — any processing step that fails
    persistently (a blocking webhook, rspamd, …) lands here.
    """
    ctx.postmark["processing"] = "fail"


@celery_app.task(
    bind=True,
    time_limit=_INBOUND_TASK_TIME_LIMIT,
    soft_time_limit=_INBOUND_TASK_SOFT_TIME_LIMIT,
)
def process_inbound_message_task(self, inbound_message_id: str):
    """Process an inbound message: run the pipeline, persist the result.

    Returns ``{"success": ...}`` so the 5-min retry sweep can tell which
    messages still need work. On DROP, the ``InboundMessage`` row is
    deleted (we're done with it) and the task reports success.
    """
    # Redis lock keyed on the message id prevents two workers from racing on
    # the same row. Its TTL is the task's hard time limit + 60s, so a live
    # task (which Celery kills at the hard limit) can never have its lock
    # stolen, while a crashed/OOM'd worker's lock still auto-frees for the
    # next sweep.
    lock_key = f"process_inbound_message_lock:{inbound_message_id}"
    if not cache.add(lock_key, "locked", _INBOUND_TASK_LOCK_TTL):
        logger.warning(
            "InboundMessage %s is already being processed — skipping",
            inbound_message_id,
        )
        return {"success": False, "error": "Message already being processed"}

    inbound_message: Optional[models.InboundMessage] = None
    # Bound up-front so the except handlers below can safely read it even if a
    # timeout/error fires before the pipeline builds it.
    ctx: Optional[InboundContext] = None
    try:
        try:
            inbound_message = models.InboundMessage.objects.get(id=inbound_message_id)
        except models.InboundMessage.DoesNotExist:
            error_msg = f"InboundMessage with ID '{inbound_message_id}' does not exist"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        if inbound_message.abandoned_at is not None:
            # Terminally failed on an earlier attempt. The sweep already
            # excludes these; this guards a direct re-dispatch so a poison
            # message can never resume looping the pipeline.
            return {
                "success": False,
                "inbound_message_id": str(inbound_message_id),
                "error": "abandoned",
            }

        # Stamp the attempt before doing any work. ``updated_at`` is what the
        # sweep backs off from, so a run that outlives a sweep interval (the
        # soft limit is 9 min, the interval 5) must not be selected again while
        # it is still going: those dispatches would only bounce off the lock
        # above and burn the batch. Costs one UPDATE on the happy path, on a
        # row that is about to be deleted anyway.
        inbound_message.save(update_fields=["updated_at"])

        raw_data_bytes = inbound_message.get_raw_bytes()
        parsed_email = parse_email(raw_data_bytes)
        if parsed_email is None:
            # Deterministic: the parse is a pure function of the stored
            # bytes, so deferring would just repeat it for 48h.
            #
            # ``error`` rather than ``warning``: abandoned rows are hard
            # deleted by the 7-day purge, so a parser regression would
            # otherwise drop real mail with no alert.
            logger.error(
                "Inbound message %s could not be parsed; abandoning (not retryable)",
                inbound_message.id,
            )
            return _abandon(inbound_message, "Failed to parse email message")

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

        # On a retry attempt (the row has been processed before, so it carries
        # an ``error_message``) replay the blocking-webhook results memoised on
        # the previous attempt — so a sustained downstream failure (e.g. rspamd
        # down) doesn't re-POST every already-succeeded blocking webhook on
        # each 5-min sweep. The happy path (first attempt) skips this read.
        if inbound_message.error_message:
            ctx.blocking_webhook_results = load_cached_webhook_results(
                str(inbound_message.id)
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
        deferral_expired = False
        if decision == Decision.RETRY:
            age = timezone.now() - inbound_message.created_at
            if age <= DEFERRAL_MAX_AGE:
                # About to hold for retry: persist the blocking webhooks that
                # DID succeed this round so the next attempt replays them
                # instead of re-POSTing. Written only here, on the retry path —
                # the happy path never touches Redis.
                persist_cached_webhook_results(
                    str(inbound_message.id), ctx.blocking_webhook_results
                )
                return _handle_retry(inbound_message, aborted_by)
            # The deferral window has expired: a processing step (blocking
            # webhook, rspamd, …) has failed persistently. Stop holding —
            # deliver the message anyway so it's never lost, but stamp it
            # so the UI warns the recipient it bypassed a processing step,
            # and land it in the inbox (is_spam=False) so the warning is
            # actually seen rather than buried in the spam folder.
            logger.warning(
                "Inbound message %s force-delivered (deferral window expired) "
                "after persistent failure at step=%s (age=%s)",
                inbound_message_id,
                aborted_by,
                age,
            )
            _stamp_processing_failed(ctx)
            deferral_expired = True
            # The message is being forced to the inbox, so it is no longer
            # treated as spam. Normalize ctx.is_spam so downstream consumers
            # (autoreply gate, task result) agree with where it actually lands.
            ctx.is_spam = False
            # ...but force-delivering past an expired deferral means a
            # processing step never completed: the forced is_spam=False is a
            # placement decision, not a real spam verdict, and a blocking step
            # that wanted to suppress the reply (or classify the sender as
            # spam) never got to run. Don't fire an autoreply to a sender we
            # couldn't fully vet — suppress it when the deferral window expired.
            ctx.skip_autoreply = True

        # Create the Message and drop the queue row as one unit: either the
        # message persists and the InboundMessage is gone, or neither is. This
        # closes the crash window where the message committed but the queue row
        # survived, leaving the 5-min sweep to reprocess and re-run the
        # one-shot finalize side effects below.
        # Record the envelope RCPT TO in postmark when it diverges from the MIME
        # To/Cc (alias / BCC / catch-all). This is an inbound-only signal — it
        # needs the real SMTP envelope, which only this queue path has — so it's
        # built here alongside the pipeline's other postmark verdicts, not down
        # in the shared ``_create_message_from_inbound`` (which also serves
        # imports and outbound, where no envelope RCPT exists). Fall back to the
        # canonical address only when the envelope is absent (old in-flight rows).
        _record_divergent_rcpt(
            ctx.postmark,
            (inbound_message.envelope or {}).get("rcpt_to") or ctx.recipient_email,
            ctx.parsed_email,
        )

        with transaction.atomic():
            inbound_msg = _create_message_from_inbound(
                recipient_email=ctx.recipient_email,
                parsed_email=ctx.parsed_email,
                raw_data=ctx.raw_data,
                mailbox=mailbox,
                channel=inbound_message.channel,
                is_spam=False if deferral_expired else bool(ctx.is_spam),
                is_trashed=ctx.mark_trashed,
                is_archived=ctx.mark_archived,
                # Reuse the ingest blob (the bytes are never mutated —
                # verdicts go to postmark) and carry the pipeline's postmark.
                blob=inbound_message.blob,
                postmark=ctx.postmark,
            )
            if inbound_msg:
                inbound_message.delete()

        if inbound_msg:
            # Run the finalize side effects only when THIS call created the
            # message. ``_create_message_from_inbound`` returns the existing
            # row with ``_created_now=False`` whenever it dedups on
            # ``(mailbox, mime_id)`` — most commonly a DUPLICATE INBOUND EMAIL:
            # an upstream MTA redelivers the same Message-ID (SMTP retry,
            # greylisting, a relay double-sending), so we get a second
            # ``InboundMessage`` and process it later. (A concurrent second
            # task could also land here, but is structurally prevented in
            # practice — the prefork hard ``time_limit`` kills a task before
            # its lock TTL frees; see ``process_inbound_message_task``.) Either
            # way the side effects already ran for the original create, so
            # repeating them here would duplicate them.
            #
            # The gate (not any inherent idempotency) is what makes this safe:
            # events create a ThreadEvent, drafts create a Message, the
            # autoreply SENDS an email, and the non-blocking webhook POSTs
            # ``message.delivered`` to the receiver — all external, none
            # idempotent. (Labels / assigns / flags happen to be idempotent and
            # could run unconditionally, but are gated with the rest for
            # simplicity — there is nothing new to apply on a dedup hit anyway.)
            # NB: ``message.delivered`` is independently at-least-once at the
            # Celery layer; this only stops a duplicate *enqueue* on reprocess.
            created_now = isinstance(inbound_msg, models.Message) and getattr(
                inbound_msg, "_created_now", False
            )

            if created_now:
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

            if created_now and not ctx.skip_autoreply:
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
                        envelope=inbound_message.envelope,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception(
                        "Autoreply failed for inbound message %s", inbound_message_id
                    )

            # Truly last step: fire-and-forget push now that the message is
            # fully delivered. Gated on `created_now` like every other side
            # effect above: on a dedup hit (SMTP retry, greylisting) the push
            # already fired for the original create and would otherwise re-alert
            # the device — `enqueue_push_notifications` has no idempotency of
            # its own. Spam is skipped: no point waking a device for it.
            # `enqueue_push_notifications` already no-ops when push is
            # disabled ("safe to call unconditionally"), so no extra gate here.
            if created_now and not ctx.is_spam:
                enqueue_push_notifications(inbound_msg)

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
        # bounded retry rather than keeping the row forever — carrying the
        # already-run blocking webhooks so the retry doesn't re-POST them.
        return _retry_or_abandon(
            inbound_message,
            "Failed to create message from inbound message",
            blocking_webhook_results=ctx.blocking_webhook_results,
        )

    except SoftTimeLimitExceeded:
        # The task ran past its soft time limit (almost always a slow chain of
        # blocking webhooks). Bail out gracefully while we still can — before
        # the hard limit SIGKILLs us — so the ``finally`` below releases the
        # lock cleanly. Hold for retry: a message that *always* overruns
        # (e.g. far too many slow blocking webhooks) is bounded by the same
        # deferral window and ends up abandoned (kept + marked) rather than
        # looping forever.
        logger.warning(
            "Inbound message %s exceeded the %ss soft time limit — holding for retry",
            inbound_message_id,
            _INBOUND_TASK_SOFT_TIME_LIMIT,
        )
        # A soft timeout fires asynchronously and can surface in the small
        # unwrapped gaps between the post-delete finalize blocks. Once the queue
        # row is deleted ``delete()`` nulls its pk, so ``_retry_or_abandon`` would
        # ``save(update_fields=...)`` a pk-less row and raise ValueError, masking
        # this failure. ``pk is None`` is exactly that precondition: skip retry.
        if inbound_message and inbound_message.pk is not None:
            return _retry_or_abandon(
                inbound_message,
                f"Processing exceeded the {_INBOUND_TASK_SOFT_TIME_LIMIT}s "
                "soft time limit",
                blocking_webhook_results=ctx.blocking_webhook_results if ctx else None,
            )
        return {"success": False, "error": "soft_time_limit"}
    except Exception as e:
        # Sanitized for Sentry: log only the exception *type*, never ``str(e)``
        # nor ``exc_info``. ``logger.exception`` would ship the traceback with
        # its frame locals (the parsed email, addresses, body) to Sentry — an
        # external service. The full ``str(e)`` is preserved instead on the
        # internal row (``error_message`` / Celery result) below.
        logger.error(
            "Error processing inbound message %s: %s",
            inbound_message_id,
            type(e).__name__,
        )
        # ``pk is None`` ⇒ the row was already deleted (message committed) and a
        # post-delete exception slipped through; retry/abandon would save a
        # pk-less row and raise ValueError, masking this failure — skip it.
        if inbound_message and inbound_message.pk is not None:
            # Same bounded-retry policy as a failed creation: a persistent
            # error must not pin the row (and re-fire webhooks) forever.
            # ``str(e)`` is kept in full: it lands in the admin-visible
            # ``error_message`` and the Celery result backend — both internal,
            # trusted stores an operator inspects to diagnose the row. What we
            # keep OUT is Sentry (external): the ``logger.error`` above is
            # sanitized to the exception type only, so no raw mail fragment
            # (addresses, subject, body via frame locals) leaves our infra.
            return _retry_or_abandon(
                inbound_message,
                str(e),
                blocking_webhook_results=ctx.blocking_webhook_results if ctx else None,
            )
        return {"success": False, "error": str(e)}
    finally:
        # Always release the lock
        cache.delete(lock_key)


@celery_app.task(bind=True)
def process_inbound_messages_queue_task(
    self, chunk_size: int = 500, max_dispatch: int = 2000
):
    """Re-dispatch inbound messages whose next retry is due.

    This task only handles retries for messages that may have failed or gotten stuck.
    Regular messages are processed immediately when created via process_inbound_message_task.delay().

    Due-ness is per ``_RETRY_BACKOFF``, so a row that keeps failing is asked
    less and less often. Ordering by ``updated_at`` takes the
    least-recently-attempted first: with a flat schedule and ``created_at``
    ordering, a backlog larger than the batch let the oldest rows hold every
    slot forever while newer ones aged out to the deferral window without ever
    being retried once. It also means a run cut short by ``max_dispatch``
    drops the rows that have waited least, never the same rows every tick.

    Dispatches the whole due set rather than one truncated slice: a backlog
    bigger than the slice used to cap retry throughput regardless of how much
    capacity the workers had. ``max_dispatch`` bounds a single run so one tick
    cannot enqueue an unbounded burst; hitting it is logged, never silent.

    Streamed with ``.iterator()`` (server-side cursor) over a ``values_list``
    of ids, so neither the row count nor the message size lands in worker RAM
    — the same idiom as ``re_store_blobs``. One snapshot, so a row whose
    ``updated_at`` is stamped by its worker mid-sweep can neither be handed
    back nor skipped.

    Args:
        chunk_size: Rows fetched per round trip
        max_dispatch: Maximum messages re-dispatched in one run

    Returns:
        dict: A dictionary with processing results
    """
    due = (
        models.InboundMessage.objects.filter(
            _due_for_retry_q(timezone.now()),
            # Terminally-failed rows are kept for inspection/replay but must
            # not be retried — otherwise the poison message loops the pipeline
            # (and re-fires every user webhook) every 5 minutes forever.
            abandoned_at__isnull=True,
        )
        .order_by("updated_at")
        .values_list("id", flat=True)[: max_dispatch + 1]
    )

    processed = 0
    errors = 0
    total = 0
    capped = False

    for row_id in due.iterator(chunk_size=chunk_size):
        if total >= max_dispatch:
            # The extra row the slice asked for: more work is due than this
            # run will take.
            capped = True
            break
        total += 1
        try:
            # Trigger async task for each old message (retry)
            process_inbound_message_task.delay(str(row_id))
            processed += 1
        except Exception as e:
            logger.exception(
                "Error queuing inbound message %s for retry: %s",
                row_id,
                e,
            )
            errors += 1

    if capped:
        logger.warning(
            "Inbound retry sweep stopped at its %d-message cap — more were due; "
            "the queue is not draining within one run",
            max_dispatch,
        )

    return {
        "success": True,
        "processed": processed,
        "errors": errors,
        "total": total,
    }


# How long an abandoned InboundMessage (``abandoned_at`` set) is kept before
# the purge sweep reclaims it: long enough for an operator to act on the
# Sentry alert (inspect / replay from the admin), short enough that a stream
# of poison mail can't grow the transient queue table without bound.
_ABANDONED_RETENTION = timezone.timedelta(days=7)


@celery_app.task(bind=True)
def purge_abandoned_inbound_messages_task(
    self, batch_size: int = 500, max_batches: int = 200
):
    """Reclaim inbound messages abandoned more than ``_ABANDONED_RETENTION`` ago.

    Abandoned rows are deliberately kept (never deleted at abandon time) so the
    mail stays inspectable / replayable — see ``_retry_or_abandon``. But they
    must not accumulate forever: a sustained stream of unparseable / uncreatable
    mail would otherwise grow this transient queue table (and pin the blobs
    it references) without bound. This daily sweep deletes rows past the
    retention window.

    Deletes in batches through ``QuerySet.delete()`` (not ``_raw_delete``) so
    the ``post_delete`` signal fires per row and any referenced blob is
    scheduled for GC. ``max_batches`` caps a single run, so a large backlog
    (e.g. after an abuse spike) drains over a few days instead of one giant
    locking transaction.
    """
    cutoff = timezone.now() - _ABANDONED_RETENTION
    purged = 0
    for _ in range(max_batches):
        ids = list(
            models.InboundMessage.objects.filter(
                abandoned_at__isnull=False,
                abandoned_at__lt=cutoff,
            )
            .order_by("abandoned_at")
            .values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            break
        models.InboundMessage.objects.filter(id__in=ids).delete()
        purged += len(ids)

    if purged:
        logger.info(
            "Purged %s abandoned inbound message(s) older than %s",
            purged,
            _ABANDONED_RETENTION,
        )
    return {"success": True, "purged": purged}

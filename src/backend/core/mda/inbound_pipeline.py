"""Inbound-message processing pipeline.

Every "thing we do with an incoming message before it lands as a
``Message`` row" is a **Step**: a callable that takes an
``InboundContext`` and returns a ``Decision``. Steps may also mutate
the context — set ``is_spam``, add ``labels``, cache ``rspamd_result``,
record an auth verdict in ``postmark``, etc.

``build_inbound_pipeline`` assembles the ordered step list for a message —
before-spam user webhooks, the hardcoded-rules and rspamd spam checks, the
inbound-auth (DKIM/DMARC) step, then after-spam user webhooks.
``run_inbound_pipeline`` iterates that list and aborts on the first step
that returns a non-``CONTINUE`` ``Decision`` (``DROP`` / ``RETRY``); the
caller turns that decision into a task-level return value.

This file deliberately knows nothing about HTTP, JWT, or JMAP — those
live in ``dispatch_webhooks.py`` behind ``UserWebhookStep``. The
pipeline only sees the uniform ``Step → Decision`` interface.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from django.conf import settings
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from jmap_email import JmapEmail

from core import enums, models
from core.mda import spam
from core.mda.arc import arc_result
from core.mda.inbound_auth import (
    check_inbound_authentication,
    get_inbound_auth_mode,
    trusted_arc_sealers,
)
from core.services.thread_events import assign_users

logger = logging.getLogger(__name__)


class Decision(IntEnum):
    """Step control-flow signal.

    Ordered ``DROP > RETRY > CONTINUE``. The pipeline aborts on the
    first non-CONTINUE.
    """

    CONTINUE = 0
    RETRY = 1
    DROP = 2


@dataclass
class InboundContext:  # pylint: disable=too-many-instance-attributes
    """Mutable bag of state flowing through the pipeline.

    Steps read what they need and write what they decide. The post-loop
    finalizer reads the final values (``is_spam``, ``labels``,
    ``parsed_email``, ``raw_data``) to build the ``Message`` row.
    """

    mailbox: models.Mailbox
    inbound_message: models.InboundMessage
    recipient_email: str
    raw_data: bytes
    parsed_email: JmapEmail
    spam_config: Dict[str, Any]

    # Verdict, accumulated across steps:
    # - None: undecided (no spam step has run, or none had an opinion)
    # - True/False: the last decisive step wins
    is_spam: Optional[bool] = None

    # Sparse pipeline record written to ``Message.postmark`` at finalize time.
    # Steps add flat keys ("auth", "processing", ...) rather than prepending
    # X-StMsg-* to the bytes, so the ingest blob is reused untouched. Empty on
    # the happy path (finalize stores NULL, not {}).
    postmark: Dict[str, Any] = field(default_factory=dict)

    # Labels webhook receivers have asked us to attach to the thread.
    # Validated against the destination mailbox at finalize time;
    # unknown UUIDs are dropped silently.
    labels: Set[str] = field(default_factory=set)

    # Deferred per-channel assign requests from blocking webhooks. Each
    # entry is ``(channel_id, [oidc_email, ...])`` — applied AFTER the
    # message + thread exist, one ``ThreadEvent ASSIGN`` per entry so
    # the audit trail keeps each channel's contribution separate.
    pending_assigns: List[Tuple[Any, List[str]]] = field(default_factory=list)

    # Deferred per-channel ThreadEvents to create after the thread
    # exists. Each entry is ``(channel_id, event_dict)`` — currently
    # only ``type=im`` events flow here, but the structure is
    # forward-compatible for future event types (e.g. ``iframe``).
    pending_events: List[Tuple[Any, Dict[str, Any]]] = field(default_factory=list)

    # Deferred per-channel ``reply_draft`` requests. Each entry is
    # ``(channel_id, template_id)`` — applied AFTER message + thread
    # exist; resolves the template (scope-checked against the mailbox /
    # maildomain) and materialises one draft Message per entry via
    # the autoreply path's shared record helper.
    pending_drafts: List[Tuple[Any, str]] = field(default_factory=list)

    # Deferred non-blocking webhook dispatches. Each entry is
    # ``(channel_id, is_spam)`` — recorded when the webhook step runs and
    # fired AFTER the Message exists, so the task renders the payload from
    # the durable ``Message`` (no transient snapshot, nothing large on the
    # broker). ``message.delivered`` always runs after the spam step, so the
    # recorded verdict is final.
    pending_webhooks: List[Tuple[Any, Optional[bool]]] = field(default_factory=list)

    # Blocking-webhook flag actions (OR-merged across webhooks). All
    # default to False and are only ever flipped to True by a
    # receiver explicitly opting in via the JSON action body. The
    # task body applies them to ThreadAccess / Message / autoreply
    # after the message is created.
    mark_starred: bool = False
    mark_read: bool = False
    mark_trashed: bool = False
    mark_archived: bool = False
    skip_autoreply: bool = False

    # Populated by ``rspamd_step`` so ``inbound_auth_step`` can reuse
    # the symbols (DKIM/DMARC verdicts) without a second HTTP call.
    rspamd_result: Optional[Dict[str, Any]] = None

    # Populated by ``arc_gate_step`` so ``inbound_auth_step`` can reuse it.
    arc: Optional[Dict[str, Any]] = None

    # Memoised results of blocking webhook steps, keyed by
    # ``(channel_id, phase)``. Pre-loaded from Redis at the start of a
    # *retry* attempt so an already-succeeded blocking webhook is replayed
    # from cache instead of re-POSTed — without this, a sustained rspamd
    # outage (which RETRYs after the before-spam webhooks have run) would
    # re-fire every before-spam webhook on each 5-min sweep, hundreds of
    # times. Empty on the happy path; the task persists it back only when it
    # decides to RETRY. Values are opaque ``_HttpResult`` objects owned by
    # ``dispatch_webhooks`` — carried here, not interpreted, to avoid an
    # import cycle.
    blocking_webhook_results: Dict[Tuple[str, str], Any] = field(default_factory=dict)


# A Step is just a callable. It MUST have a ``.name`` attribute so
# logs and the task return value can report which step aborted.
Step = Callable[[InboundContext], Decision]


# Inbound messages held by a transient RETRY get one more chance every
# 5 minutes via ``process_inbound_messages_queue_task``. RETRY is produced
# by a blocking webhook step (transport failure / non-2xx) or by the rspamd
# step (spam-check outage), and is bounded by the deferral window below —
# so a held message is never dropped, only delivered (flagged) once it
# gives up.
#
# A processing step that keeps failing (a blocking webhook or rspamd today,
# any future RETRY-returning step tomorrow) must not hold a message
# forever *or* silently lose it. After this window the task stops holding
# and delivers the message anyway, recording ``postmark["processing"]`` so
# the UI warns the recipient it bypassed a processing step.
# Generic on purpose — see the RETRY branch in
# ``process_inbound_message_task``. Operator-tunable via
# ``MESSAGES_INBOUND_DEFERRAL_MAX_AGE`` (seconds; default 48h).
DEFERRAL_MAX_AGE = timedelta(seconds=settings.MESSAGES_INBOUND_DEFERRAL_MAX_AGE)


# ---------------------------------------------------------------------------
# Steps. Each is callable as ``step(ctx) -> Decision`` and carries a
# ``.name`` for log/return-value reporting.
# ---------------------------------------------------------------------------


def _make_arc_gate_step(spam_config: Dict[str, Any]) -> Step:
    action = str(spam_config.get("arc_gate") or "off").strip().lower()

    def arc_gate(ctx: InboundContext) -> Decision:
        if action == "off":
            return Decision.CONTINUE
        ctx.arc = arc_result(ctx.raw_data, trusted_arc_sealers(spam_config))
        if ctx.arc["trusted"] or ctx.arc["dnsfail"]:
            return Decision.CONTINUE
        logger.info(
            "ARC gate: untrusted message (sealer=%s) -> %s",
            ctx.arc["sealer"],
            action,
        )
        if action == "drop":
            return Decision.DROP
        if action == "spam" and ctx.is_spam is None:
            ctx.is_spam = True
        return Decision.CONTINUE

    arc_gate.name = "arc_gate"
    return arc_gate


def _make_hardcoded_rules_step(spam_config: Dict[str, Any]) -> Step:
    def hardcoded_rules(ctx: InboundContext) -> Decision:
        if ctx.is_spam is not None:
            return Decision.CONTINUE
        verdict = spam.check_hardcoded_rules(ctx.parsed_email, spam_config)
        if verdict is not None:
            ctx.is_spam = verdict
        return Decision.CONTINUE

    hardcoded_rules.name = "hardcoded_rules"
    return hardcoded_rules


def _make_rspamd_step(spam_config: Dict[str, Any]) -> Step:
    """Rspamd as a step.

    Maps the rspamd action onto a pipeline decision. Always caches the full
    ``rspamd_result`` dict on the context — ``inbound_auth_step`` reuses the
    symbols (DKIM/DMARC) without a second HTTP call. The full action set
    (https://docs.rspamd.com/configuration/metrics/):

    * ``no action`` → deliver (is_spam=False).
    * ``add header`` / ``rewrite subject`` → deliver to the inbox with a
      graded ``postmark["spam"]`` marker (possible / likely) for the UI, not
      hidden in Junk.
    * ``quarantine`` / ``reject`` → spam verdict (is_spam=True → Junk). We
      can't honour ``reject`` at SMTP time (already accepted), so it lands in
      Junk.
    * ``greylist`` / ``soft reject`` → temporary failures, NOT verdicts: route
      onto our deferral path (RETRY). The condition (rate-limit, greylist,
      transient DNS) usually clears within the 5-min sweep; a persistent one
      force-delivers flagged past ``DEFERRAL_MAX_AGE`` (postmark processing).
    * ``discard`` → rspamd asks us to accept-and-silently-drop (blackhole, no
      bounce). Honour it as DROP — the message is consumed, nothing created.

    An rspamd *error* never fails open: we don't deliver mail that couldn't be
    spam-checked. The step RETRYs, so the message is held; if the outage lasts
    past ``DEFERRAL_MAX_AGE`` it is force-delivered flagged rather than
    silently unchecked. (rspamd not being configured is not an error —
    ``spam.call_rspamd`` returns no opinion and the pipeline moves on.)
    """

    def rspamd(ctx: InboundContext) -> Decision:
        if ctx.is_spam is not None:
            # Spam verdict already decided — but we still might want
            # rspamd's symbols for inbound_auth. The auth step has its
            # own fallback so we can cheaply skip rspamd entirely here.
            return Decision.CONTINUE
        action, err, result = spam.call_rspamd(
            ctx.raw_data, spam_config, envelope=ctx.inbound_message.envelope
        )
        if err:
            # Don't fail open — hold for retry rather than deliver
            # unchecked. A sustained outage is bounded by the deferral
            # window (the message is then delivered flagged).
            logger.warning(
                "rspamd error on inbound message %s: %s (holding for retry)",
                ctx.inbound_message.id,
                err,
            )
            return Decision.RETRY
        ctx.rspamd_result = result
        if action is None:
            # rspamd not configured — no opinion, leave the verdict undecided.
            return Decision.CONTINUE

        # --- The single source of truth for rspamd action → outcome. ---
        if action in ("greylist", "soft reject"):
            # Temporary failure, not a verdict — defer and re-evaluate on the
            # sweep (converges on the same deferral-expiry force-delivery as
            # any persistent processing failure).
            logger.info(
                "rspamd '%s' on inbound message %s — holding for retry",
                action,
                ctx.inbound_message.id,
            )
            return Decision.RETRY
        if action == "discard":
            # rspamd blackhole: accept-and-drop, no delivery, no bounce.
            logger.info(
                "rspamd 'discard' on inbound message %s — dropping",
                ctx.inbound_message.id,
            )
            return Decision.DROP
        # Milder flag actions deliver to the INBOX with a graded "suspected
        # spam" marker for the UI (like the suspicious-sender banner) — not
        # hidden in Junk. rspamd scores "add header" below "rewrite subject",
        # so preserve that gradient: possible < likely.
        if action == "add header":
            ctx.postmark["spam"] = "possible"
        elif action == "rewrite subject":
            ctx.postmark["spam"] = "likely"
        # Junk only for the high-confidence isolate actions; everything else
        # (no action, the flagged-but-delivered actions above, unknown) is
        # delivered to the inbox.
        ctx.is_spam = action in ("quarantine", "reject")
        return Decision.CONTINUE

    rspamd.name = "rspamd"
    return rspamd


def _make_inbound_auth_step(spam_config: Dict[str, Any]) -> Step:
    """DKIM / DMARC verdict via ``check_inbound_authentication``.

    Reuses ``ctx.rspamd_result`` if populated; otherwise calls rspamd
    itself when ``auth_mode='rspamd'``. On a verdict, records it in
    ``ctx.postmark["auth"]`` (structured, off the bytes) so the ingest blob
    is reused untouched; ``get_stmsg_headers`` surfaces it downstream.
    """

    def inbound_auth(ctx: InboundContext) -> Decision:
        if ctx.rspamd_result is None and get_inbound_auth_mode(spam_config) == "rspamd":
            _, _, ctx.rspamd_result = spam.call_rspamd(
                ctx.raw_data, spam_config, envelope=ctx.inbound_message.envelope
            )
        verdict = check_inbound_authentication(
            ctx.raw_data, ctx.parsed_email, spam_config, ctx.rspamd_result, ctx.arc
        )
        if not verdict:
            # Widget submissions arrive over an unauthenticated web form, so
            # they carry the "none" baseline even when DKIM/DMARC verification
            # is disabled instance-wide (which is when ``verdict`` is falsy).
            if (ctx.inbound_message.envelope or {}).get(
                "origin"
            ) == enums.InboundOrigin.WIDGET:
                ctx.postmark["auth"] = "none"
            return Decision.CONTINUE
        # ``verdict`` is already "none" (unverified) or "fail" (forged); a
        # verified message returns no verdict and leaves ``auth`` absent.
        ctx.postmark["auth"] = verdict
        return Decision.CONTINUE

    inbound_auth.name = "inbound_auth"
    return inbound_auth


# ---------------------------------------------------------------------------
# Pipeline construction + runner.
# ---------------------------------------------------------------------------


def build_inbound_pipeline(ctx: InboundContext) -> List[Step]:
    """Standard pipeline for an inbound message.

    Order matters:
      1. Before-spam user webhooks — may DROP, RETRY, or set is_spam.
      2. ``hardcoded_rules`` — header-match rules per domain config.
      3. ``rspamd`` — fills the gap if nothing decided spam yet, and
         caches symbols for the next step.
      4. ``inbound_auth`` — DKIM / DMARC verdict, may mutate parsed_email.
      5. After-spam user webhooks — see the verdict, may override it,
         may add labels, may DROP/RETRY.
    """
    # Imported here to avoid the inbound_pipeline ↔ dispatch_webhooks
    # cycle: webhook_steps_for_mailbox lives next to UserWebhookStep
    # because it instantiates one per matching channel.
    from core.mda.dispatch_webhooks import (  # pylint: disable=import-outside-toplevel
        find_webhook_channels_for_mailbox,
        webhook_steps_for_mailbox,
    )

    # Fetch the channel set once and reuse it for both phases — it's
    # identical before- and after-spam, so a second query is pure waste.
    channels = find_webhook_channels_for_mailbox(ctx.mailbox)

    # Internal mailbox-to-mailbox mail is trusted and not externally
    # authenticated: run only the user-webhook steps. The spam steps
    # would no-op anyway (the task pre-sets is_spam=False), and the auth
    # step would record a meaningless auth verdict plus do needless
    # DNS/rspamd work. Webhooks still fire on both phases so internal mail
    # is indistinguishable from external to a consumer.
    if ctx.inbound_message.is_internal:
        return [
            *webhook_steps_for_mailbox(
                ctx.mailbox, phase="before_spam", channels=channels
            ),
            *webhook_steps_for_mailbox(
                ctx.mailbox, phase="after_spam", channels=channels
            ),
        ]

    return [
        *webhook_steps_for_mailbox(ctx.mailbox, phase="before_spam", channels=channels),
        _make_arc_gate_step(ctx.spam_config),
        _make_hardcoded_rules_step(ctx.spam_config),
        _make_rspamd_step(ctx.spam_config),
        _make_inbound_auth_step(ctx.spam_config),
        *webhook_steps_for_mailbox(ctx.mailbox, phase="after_spam", channels=channels),
    ]


def run_inbound_pipeline(
    pipeline: List[Step], ctx: InboundContext
) -> Tuple[Decision, Optional[str]]:
    """Iterate the pipeline. Stop on the first non-CONTINUE decision.

    Returns ``(final_decision, aborting_step_name_or_None)``. The
    caller turns that into a Celery-task return value.
    """
    for step in pipeline:
        decision = step(ctx)
        if decision != Decision.CONTINUE:
            return decision, getattr(step, "name", step.__class__.__name__)
    return Decision.CONTINUE, None


# ---------------------------------------------------------------------------
# Finalisation: label application.
# ---------------------------------------------------------------------------


def apply_labels_to_thread(
    thread: models.Thread, mailbox: models.Mailbox, label_ids: Set[str]
) -> None:
    """Attach pipeline-collected labels to a thread.

    Each id is validated against the destination mailbox: unknown
    UUIDs are logged and skipped — a misbehaving webhook receiver
    must not stall delivery. Label IDs are already UUID-validated
    upstream (in the webhook response classifier).
    """
    for label_id in label_ids:
        try:
            label_obj = models.Label.objects.get(id=label_id, mailbox=mailbox)
        except models.Label.DoesNotExist:
            logger.warning(
                "Pipeline label %s not found for mailbox %s — skipping",
                label_id,
                mailbox.id,
            )
            continue
        thread.labels.add(label_obj)


def _resolve_assignable_users(
    thread: models.Thread, emails: List[str]
) -> List[Dict[str, Any]]:
    """Resolve OIDC emails → user dicts ready for ``assign_users``.

    A single SQL query fetches all matching users at once via
    ``email__in`` — no N+1. Ambiguity (≥2 users sharing one email) and
    unknown emails are logged and skipped. NEVER auto-creates users: a
    webhook receiver must not be able to pollute the ``User`` table.

    The survivors are then filtered to users that currently hold one
    of the assignable mailbox roles on this thread (editor / sender /
    admin) via ``ThreadAccess.editor_user_ids`` — viewers can't be
    assigned, matching the API rule.
    """
    if not emails:
        return []

    # The input is already lowercased + deduped by the classifier;
    # belt-and-suspenders dedup here in case a future caller forgets.
    # ``dict.fromkeys`` dedups while preserving input order so the
    # resolved assignee payload is deterministic.
    target_emails = list(dict.fromkeys(e.lower() for e in emails if e))
    if not target_emails:
        return []

    # ``User.email`` is NOT normalized to lowercase on save, so match
    # case-insensitively against the already-lowercased ``target_emails``.
    matches = list(
        models.User.objects.annotate(email_lower=Lower("email"))
        .filter(email_lower__in=target_emails)
        .only("id", "email", "full_name")
    )

    # Group by lowercased email to detect ambiguity per address.
    by_email: Dict[str, List[models.User]] = {}
    for user in matches:
        key = (user.email or "").lower()
        by_email.setdefault(key, []).append(user)

    candidate_ids: List[Any] = []
    candidate_users: Dict[Any, models.User] = {}
    for email in target_emails:
        bucket = by_email.get(email) or []
        if not bucket:
            # Don't log the raw webhook-supplied email (PII) — reference the
            # thread instead.
            logger.warning(
                "Webhook assignee email does not resolve to any user on "
                "thread %s — skipping",
                thread.id,
            )
            continue
        if len(bucket) > 1:
            logger.warning(
                "Webhook assignee email is ambiguous (multiple matches) on "
                "thread %s — skipping",
                thread.id,
            )
            continue
        user = bucket[0]
        if user.id in candidate_users:
            continue
        candidate_users[user.id] = user
        candidate_ids.append(user.id)

    if not candidate_ids:
        return []

    assignable_ids = set(
        models.ThreadAccess.objects.editor_user_ids(thread.id, user_ids=candidate_ids)
    )
    for uid in candidate_ids:
        if uid not in assignable_ids:
            # Reference the thread, not the user's email (PII).
            logger.warning(
                "Webhook assignee lacks an assignable role on thread %s — skipping",
                thread.id,
            )

    return [
        {"id": str(uid), "name": candidate_users[uid].full_name or ""}
        for uid in candidate_ids
        if uid in assignable_ids
    ]


def apply_thread_access_flags(
    thread: models.Thread,
    mailbox: models.Mailbox,
    *,
    mark_starred: bool,
    mark_read: bool,
) -> None:
    """Apply per-mailbox flag toggles to the destination ThreadAccess.

    ``mark_starred`` sets ``starred_at`` to now; ``mark_read`` sets
    ``read_at`` to now. Both are idempotent — re-applying doesn't
    unstar / unread — and both are no-ops when the corresponding bool
    is False. The ``ThreadAccess`` row may not exist if the destination
    mailbox doesn't have one yet (rare: brand-new thread, race with
    deletion); in that case we log and skip rather than fail delivery.
    """
    if not (mark_starred or mark_read):
        return
    access = models.ThreadAccess.objects.filter(thread=thread, mailbox=mailbox).first()
    if access is None:
        logger.warning(
            "ThreadAccess missing for thread %s / mailbox %s — "
            "skip mark_starred/mark_read",
            thread.id,
            mailbox.id,
        )
        return
    update_fields: List[str] = []
    now = timezone.now()
    if mark_starred and access.starred_at is None:
        access.starred_at = now
        update_fields.append("starred_at")
    if mark_read and access.read_at is None:
        access.read_at = now
        update_fields.append("read_at")
    if update_fields:
        access.save(update_fields=update_fields)


def _channels_by_id(channel_ids: List[Any]) -> Dict[Any, models.Channel]:
    """Resolve a batch of pending-action channel ids in a single query.

    The deferred-apply helpers below each carry ``(channel_id, …)`` tuples;
    resolving them one ``.get()`` at a time is an N+1 on the finalize hot
    path. A channel absent from the returned map vanished mid-processing
    (admin churn between dispatch and finalize) and the caller skips it.
    """
    return {c.id: c for c in models.Channel.objects.filter(id__in=set(channel_ids))}


def apply_pending_drafts(
    inbound_msg: models.Message,
    mailbox: models.Mailbox,
    pending: List[Tuple[Any, str]],
) -> None:
    """Materialise webhook-driven reply drafts.

    For each ``(channel_id, template_id)`` entry: look up the
    ``MessageTemplate`` scoped to the destination mailbox or its
    maildomain (out-of-scope templates are silently skipped — a
    webhook receiver mustn't be able to draft from another mailbox's
    template). Then delegate to ``create_draft_reply_from_template``,
    which shares its record-creation path with the autoreply flow and
    stores the template's editor-format body as ``draft_blob`` so the
    user can refine the draft inline.
    """
    # Inline: autoreply → outbound → inbound → inbound_tasks →
    # inbound_pipeline is a real import cycle, so this one import can't
    # move to the top.
    from core.mda.autoreply import (  # pylint: disable=import-outside-toplevel
        create_draft_reply_from_template,
    )

    channels = _channels_by_id([cid for cid, _ in pending])
    for channel_id, template_id in pending:
        template = (
            models.MessageTemplate.objects.filter(
                Q(mailbox=mailbox) | Q(maildomain=mailbox.domain),
                id=template_id,
                type=enums.MessageTemplateTypeChoices.MESSAGE,
                is_active=True,
            )
            .select_related("blob", "signature__blob")
            .first()
        )
        if template is None:
            logger.warning(
                "Webhook reply_draft template %s not found or out of scope "
                "for mailbox %s — skipping",
                template_id,
                mailbox.id,
            )
            continue
        channel = channels.get(channel_id)
        if channel is None:
            logger.warning(
                "Webhook channel %s vanished before reply_draft could land — skipping",
                channel_id,
            )
            continue
        create_draft_reply_from_template(
            template,
            mailbox,
            inbound_msg,
            channel=channel,
        )


def apply_pending_events(
    thread: models.Thread, pending: List[Tuple[Any, Dict[str, Any]]]
) -> None:
    """Persist webhook-driven ``ThreadEvent`` rows.

    One row per ``(channel_id, event_dict)`` pair — preserves per-
    receiver attribution. Today only ``type=im`` events arrive here
    (the classifier dropped unknown types); future types just need
    their dispatch case added without touching the contract.
    """
    channels = _channels_by_id([cid for cid, _ in pending])
    for channel_id, event in pending:
        event_type = event.get("type")
        if event_type != enums.ThreadEventTypeChoices.IM:
            logger.warning("Unknown pending event type %r — skipping", event_type)
            continue
        channel = channels.get(channel_id)
        if channel is None:
            logger.warning(
                "Webhook channel %s vanished before event could land — skipping",
                channel_id,
            )
            continue
        models.ThreadEvent.objects.create(
            thread=thread,
            author=None,
            channel=channel,
            type=enums.ThreadEventTypeChoices.IM,
            data={
                "content": event["content"],
                "mentions": event.get("mentions", []),
            },
        )


def apply_pending_assigns(
    thread: models.Thread, pending: List[Tuple[Any, List[str]]]
) -> None:
    """Replay the per-channel deferred assigns into ``ThreadEvent``s.

    One ``assign_users()`` call per (channel, emails) tuple → one
    ``ThreadEvent ASSIGN`` per webhook that asked. The service's
    idempotence (partial UniqueConstraint on UserEvent) absorbs a
    later webhook re-asking for an already-assigned user, so the
    first-to-ask is the canonical attribution.
    """
    channels = _channels_by_id([cid for cid, _ in pending])
    for channel_id, emails in pending:
        assignees_data = _resolve_assignable_users(thread, emails)
        if not assignees_data:
            continue
        channel = channels.get(channel_id)
        if channel is None:
            # The webhook channel was deleted between dispatch and
            # finalize (admin churn during processing). Skip the
            # assign rather than half-attribute it to a dead row.
            logger.warning(
                "Webhook channel %s vanished before assign could land — skipping",
                channel_id,
            )
            continue
        try:
            assign_users(
                thread=thread,
                author=None,
                assignees_data=assignees_data,
                channel=channel,
            )
        except ValueError as exc:
            # Editor-rights check inside the service. We already
            # pre-filtered, so this shouldn't fire — but if a race
            # invalidated the rights between filter and service call,
            # don't blow up delivery over it.
            # Log only the exception type — the message can embed assignee
            # emails or names and leak user-identifying details into logs.
            logger.warning(
                "assign_users skipped %d assignee(s) due to race (%s)",
                len(assignees_data),
                type(exc).__name__,
            )

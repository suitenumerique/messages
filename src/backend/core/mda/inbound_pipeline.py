"""Inbound-message processing pipeline.

Every "thing we do with an incoming message before it lands as a
``Message`` row" is a **Step**: a callable that takes an
``InboundContext`` and returns a ``Decision``. Steps may also mutate
the context — set ``is_spam``, add ``labels``, cache ``rspamd_result``,
prepend an authentication header, etc.

  pipeline = [
      *user_webhook_steps(mailbox, phase="before_spam"),
      hardcoded_rules_step(spam_config),
      rspamd_step(spam_config),
      inbound_auth_step(spam_config),
      *user_webhook_steps(mailbox, phase="after_spam"),
  ]
  for step in pipeline:
      d = step(ctx)
      if d != Decision.CONTINUE:
          break

The orchestrator (``run_inbound_pipeline``) iterates and aborts on the
first ``DROP`` / ``RETRY``. The caller turns that decision into a
task-level return value.

This file deliberately knows nothing about HTTP, JWT, or JMAP — those
live in ``dispatch_webhooks.py`` behind ``UserWebhookStep``. The
pipeline only sees the uniform ``Step → Decision`` interface.
"""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

import requests
from jmap_email import JmapEmail, has_header, parse_email

from core import enums, models
from core.mda.inbound_auth import (
    check_inbound_authentication,
    get_inbound_auth_mode,
)
from core.mda.utils import headers_blocks
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


# A Step is just a callable. It MUST have a ``.name`` attribute so
# logs and the task return value can report which step aborted.
Step = Callable[[InboundContext], Decision]


# Inbound messages held by a transient RETRY get one more chance every
# 5 minutes via ``process_inbound_messages_queue_task``. After this cap
# we drop and log loudly so a permanently-broken receiver can't pin a
# row in the queue forever.
RETRY_MAX_AGE = timedelta(days=7)


# ---------------------------------------------------------------------------
# Spam-check helpers shared by the steps below.
# ---------------------------------------------------------------------------


def _check_hardcoded_rules(
    parsed_email: JmapEmail, spam_config: Dict[str, Any]
) -> Optional[bool]:
    """Apply the per-domain hardcoded ``rules`` list, header-matched
    only against headers from trusted relay blocks. Returns ``True`` /
    ``False`` on first matching rule, ``None`` if no rule matched."""
    rules = spam_config.get("rules", [])
    for idx, rule in enumerate(rules):
        header_match = rule.get("header_match") or rule.get("header_match_regex")
        if not header_match:
            continue
        if ":" not in header_match:
            # Log the rule position, not its raw value: ``spam_config``
            # also carries spam-service credentials, so we never echo
            # values read from it into logs.
            logger.warning(
                "Invalid header_match format (missing colon) in spam rule #%d", idx
            )
            continue
        key, value = header_match.split(":", 1)
        key = key.lower().strip()
        value = value.lower().strip()

        # Existence check first; the trusted value is read from the
        # Received-bounded blocks below.
        if not has_header(parsed_email, key):
            continue

        # Trust window is "block 0 (our MTA's Received + headers above it)
        # + N upstream relay blocks". Default 0: a sender can prepend
        # their own Received lines (landing in block 1+), so trusting
        # those by default would let them forge an allowlist match.
        # Slicing beyond list length is fine — yields all blocks.
        trusted_relays = spam_config.get("trusted_relays", 0)
        blocks_to_check = trusted_relays + 1
        found_value = None
        for block in headers_blocks(parsed_email)[:blocks_to_check]:
            if key in block and block[key]:
                # Blocks are ordered most-recent → oldest; first match wins.
                found_value = block[key][0]
                break
        if found_value is None:
            continue

        header_value = (
            found_value.lower().strip()
            if isinstance(found_value, str)
            else str(found_value).lower().strip()
        )
        if rule.get("header_match"):
            is_match = header_value == value
        else:  # header_match_regex
            is_match = re.fullmatch(value, header_value) is not None
        if is_match:
            action = rule.get("action") or "spam"
            if action in ("spam", "reject"):
                return True
            if action in ("ham", "no action"):
                return False
    return None


def _call_rspamd(
    raw_data: bytes, spam_config: Dict[str, Any]
) -> Tuple[Optional[bool], Optional[str], Optional[Dict[str, Any]]]:
    """POST raw RFC-822 bytes to rspamd's ``/checkv2``.

    Returns ``(is_spam_or_None, error_message, result_dict)``. is_spam
    is ``None`` only when rspamd is not configured. Errors are
    swallowed and surfaced via the error_message channel so a flaky
    rspamd never blocks delivery (mirroring the old behaviour).
    """
    url = spam_config.get("rspamd_url")
    if not url:
        logger.debug("SPAM_CONFIG.rspamd_url not configured, skipping rspamd")
        return None, None, None

    headers = {"Content-Type": "message/rfc822"}
    auth = spam_config.get("rspamd_auth")
    if auth:
        headers["Authorization"] = auth

    try:
        response = requests.post(
            f"{url}/checkv2", data=raw_data, headers=headers, timeout=10
        )
        response.raise_for_status()
        result = response.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        # Network failures, non-2xx (raise_for_status), and a non-JSON
        # body (ValueError covers JSONDecodeError) all funnel here. We
        # don't let a flaky rspamd block delivery — fall through with
        # is_spam=False so the pipeline keeps moving. The
        # error_message channel lets the caller log loudly.
        logger.exception("Error calling rspamd: %s", exc)
        return False, str(exc), None
    except Exception as exc:
        logger.exception("Unexpected error calling rspamd: %s", exc)
        return False, str(exc), None

    if not isinstance(result, dict):
        logger.warning("rspamd returned non-object body: %r", result)
        return False, "rspamd returned non-object body", None

    action = result.get("action", "")
    score = result.get("score", 0.0)
    required = result.get("required_score", 15.0)
    is_spam = action == "reject"
    logger.info(
        "Rspamd: action=%s score=%.2f required=%.2f is_spam=%s",
        action,
        score,
        required,
        is_spam,
    )
    return is_spam, None, result


# ---------------------------------------------------------------------------
# Steps. Each is callable as ``step(ctx) -> Decision`` and carries a
# ``.name`` for log/return-value reporting.
# ---------------------------------------------------------------------------


def _make_hardcoded_rules_step(spam_config: Dict[str, Any]) -> Step:
    def hardcoded_rules(ctx: InboundContext) -> Decision:
        if ctx.is_spam is not None:
            return Decision.CONTINUE
        verdict = _check_hardcoded_rules(ctx.parsed_email, spam_config)
        if verdict is not None:
            ctx.is_spam = verdict
        return Decision.CONTINUE

    hardcoded_rules.name = "hardcoded_rules"
    return hardcoded_rules


def _make_rspamd_step(spam_config: Dict[str, Any]) -> Step:
    """Rspamd as a step.

    Sets ``is_spam`` if no earlier step decided. Always caches the
    full ``rspamd_result`` dict on the context — ``inbound_auth_step``
    reuses the symbols (DKIM/DMARC) without a second HTTP call.
    """

    def rspamd(ctx: InboundContext) -> Decision:
        if ctx.is_spam is not None:
            # Spam verdict already decided — but we still might want
            # rspamd's symbols for inbound_auth. The auth step has its
            # own fallback so we can cheaply skip rspamd entirely here.
            return Decision.CONTINUE
        is_spam, err, result = _call_rspamd(ctx.raw_data, spam_config)
        if err:
            logger.warning(
                "rspamd error on inbound message %s: %s (treating as not spam)",
                ctx.inbound_message.id,
                err,
            )
        ctx.rspamd_result = result
        if is_spam is not None:
            ctx.is_spam = is_spam
        return Decision.CONTINUE

    rspamd.name = "rspamd"
    return rspamd


def _make_inbound_auth_step(spam_config: Dict[str, Any]) -> Step:
    """DKIM / DMARC verdict via ``check_inbound_authentication``.

    Reuses ``ctx.rspamd_result`` if populated; otherwise calls rspamd
    itself when ``auth_mode='rspamd'``. On a verdict, prepends an
    ``X-StMsg-Sender-Auth`` header to both ``raw_data`` and
    ``parsed_email`` so subsequent steps + downstream consumers see it.
    """

    def inbound_auth(ctx: InboundContext) -> Decision:
        if ctx.rspamd_result is None and get_inbound_auth_mode(spam_config) == "rspamd":
            _, _, ctx.rspamd_result = _call_rspamd(ctx.raw_data, spam_config)
        verdict = check_inbound_authentication(
            ctx.raw_data, ctx.parsed_email, spam_config, ctx.rspamd_result
        )
        if not verdict:
            return Decision.CONTINUE
        prepended = f"X-StMsg-Sender-Auth: {verdict}\r\n".encode("ascii") + ctx.raw_data
        reparsed = parse_email(prepended)
        if reparsed is not None:
            ctx.parsed_email = reparsed
            ctx.raw_data = prepended
        else:
            # Keep raw_data / parsed_email in lockstep: if re-parse breaks,
            # we sacrifice the auth banner rather than corrupting the blob.
            logger.warning("Failed to re-parse after prepending X-StMsg-Sender-Auth")
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
        webhook_steps_for_mailbox,
    )

    return [
        *webhook_steps_for_mailbox(ctx.mailbox, phase="before_spam"),
        _make_hardcoded_rules_step(ctx.spam_config),
        _make_rspamd_step(ctx.spam_config),
        _make_inbound_auth_step(ctx.spam_config),
        *webhook_steps_for_mailbox(ctx.mailbox, phase="after_spam"),
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

    A single SQL query case-folds both sides (``Lower("email")``) and
    fetches all matching users at once — no N+1. Ambiguity (≥2 users
    sharing one email) and unknown emails are logged and skipped.
    NEVER auto-creates users: a webhook receiver must not be able to
    pollute the ``User`` table.

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

    matches = list(
        models.User.objects.annotate(_lemail=Lower("email"))
        .filter(_lemail__in=target_emails)
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
            logger.warning(
                "Webhook assignee email %s does not resolve to any user — skipping",
                email,
            )
            continue
        if len(bucket) > 1:
            logger.warning(
                "Webhook assignee email %s is ambiguous (multiple matches) — skipping",
                email,
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
            logger.warning(
                "Webhook assignee %s lacks an assignable role on the thread — skipping",
                candidate_users[uid].email,
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
        try:
            channel = models.Channel.objects.get(id=channel_id)
        except models.Channel.DoesNotExist:
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
    for channel_id, event in pending:
        event_type = event.get("type")
        if event_type != enums.ThreadEventTypeChoices.IM:
            logger.warning("Unknown pending event type %r — skipping", event_type)
            continue
        try:
            channel = models.Channel.objects.get(id=channel_id)
        except models.Channel.DoesNotExist:
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
    for channel_id, emails in pending:
        assignees_data = _resolve_assignable_users(thread, emails)
        if not assignees_data:
            continue
        try:
            channel = models.Channel.objects.get(id=channel_id)
        except models.Channel.DoesNotExist:
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
            logger.warning(
                "assign_users skipped %d assignee(s) due to race: %s",
                len(assignees_data),
                exc,
            )

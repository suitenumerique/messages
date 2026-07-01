"""User-configured outbound webhooks, modelled as pipeline ``Step``s.

For each delivered message the inbound pipeline (see
``inbound_pipeline.py``) iterates every webhook-type Channel that
matches the destination mailbox (``scope_level=MAILBOX``), its domain
(``scope_level=MAILDOMAIN``), or any global channel
(``scope_level=GLOBAL``). A single ``settings.trigger`` lifecycle event
(see ``enums.WebhookTrigger`` and ``docs/webhooks.md``) describes a webhook:
``message.inbound`` (before spam) and ``message.delivering`` (after spam)
run inline and can abort delivery (DROP), override the spam verdict, or
attach labels via their JSON response body (a transient failure / non-2xx
status holds the message for RETRY, but that is not a body-driven action);
``message.delivered`` is fire-and-forget after the message is created.

This file is webhook-specific: HTTP plumbing, signing (JWT or API key),
JMAP body building, SSRF-safe POST, response classification.
The pipeline-side glue is ``UserWebhookStep`` + ``webhook_steps_for_mailbox``.

The HTTP client is the shared ``SSRFSafeSession`` — webhook URLs are
attacker-controllable, so the same hostname/IP rejection rules used by
the image proxy and IMAP importer apply here too.

Two body formats are supported (see ``docs/webhooks.md``):
  - ``format="eml"`` (default): raw RFC-822 bytes, ``Content-Type:
    message/rfc822``. Webhook envelope metadata lives in ``X-StMsg-*``
    headers.
  - ``format="jmap"``: JMAP-compliant ``Email`` object (RFC 8621 §4.1)
    serialised as a single JSON document with ``Content-Type:
    application/json``. The body is a strictly compliant Email object —
    same envelope metadata in ``X-StMsg-*`` headers.
"""

# pylint: disable=broad-exception-caught,too-many-lines

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

import jwt
from jmap_email import JmapEmail, parse_email

from core import enums, models
from core.mda.inbound_pipeline import (
    DEFERRAL_MAX_AGE,
    Decision,
    InboundContext,
    Step,
)
from core.mda.webhook_payload import build_jmap_email
from core.services.ssrf import SSRFSafeSession, SSRFValidationError

from messages.celery_app import app as celery_app

logger = logging.getLogger(__name__)

# Total wall-clock budget for one webhook delivery (connect + send +
# read the capped response body). Enforced as a hard deadline across the
# streamed body read too, so a receiver that drip-feeds bytes just under
# the per-read timeout can't pin a worker indefinitely.
WEBHOOK_TIMEOUT = 30  # seconds
# Separate, tight cap on just the TCP/TLS connect phase.
WEBHOOK_CONNECT_TIMEOUT = 5  # seconds

# Hard cap on the receiver response body we parse for the action JSON.
# The contract body is tiny (action / is_spam / labels = a few hundred
# bytes at most). A bigger response is almost certainly an HTML error
# page from a misconfigured proxy — parse what we have, ignore the
# rest, never let a misbehaving receiver OOM the worker.
MAX_RESPONSE_BODY = 64 * 1024  # 64 KiB

# Per-action input caps. A receiver can't make us do unbounded work
# from one webhook call: extra entries past the cap are silently
# dropped at parse time.
MAX_LABELS_PER_RESPONSE = 50
MAX_ASSIGN_TO_PER_RESPONSE = 50
MAX_EVENTS_PER_RESPONSE = 20
MAX_IM_CONTENT_BYTES = 32 * 1024  # 32 KiB per internal-message comment

PHASE_BEFORE_SPAM = "before_spam"
PHASE_AFTER_SPAM = "after_spam"
VALID_PHASES = frozenset({PHASE_BEFORE_SPAM, PHASE_AFTER_SPAM})


@dataclass
class _HttpResult:  # pylint: disable=too-many-instance-attributes
    """Internal: one webhook call's outcome — decision + the side
    effects the receiver asked us to apply to the pipeline context.

    The ``UserWebhookStep`` applies these to its ``InboundContext``
    and returns the decision; outside this file the type is invisible.

    Bool flag fields (``mark_starred`` / ``mark_read`` / ``mark_trashed``
    / ``mark_archived`` / ``skip_autoreply``) follow ``true``-only
    semantics: a receiver returning ``true`` opts in; anything else
    (``false``, missing, non-bool) is "no opinion". This makes the
    multi-webhook merge a simple OR so a later receiver can't
    accidentally veto an earlier receiver's directive.
    """

    decision: Decision = Decision.CONTINUE
    is_spam_override: Optional[bool] = None
    labels: Set[str] = field(default_factory=set)
    # Ordered, lowercased, deduped — preserves the receiver's intent
    # while letting the pipeline use cheap set/list operations downstream.
    assign_to: List[str] = field(default_factory=list)
    mark_starred: bool = False
    mark_read: bool = False
    mark_trashed: bool = False
    mark_archived: bool = False
    skip_autoreply: bool = False
    # add_event: each entry is a validated dict ready to be persisted
    # as a ThreadEvent. Currently only ``type=im`` is supported.
    events: List[Dict[str, Any]] = field(default_factory=list)
    # reply_draft: receiver-supplied MessageTemplate UUID. Resolved +
    # scope-checked + drafted in the pipeline finalize step.
    reply_draft_template_id: Optional[str] = None

    def to_cache(self) -> Dict[str, Any]:
        """Plain JSON-able dict for the cross-retry result cache.

        Explicit (not pickle) so a deploy that changes this dataclass mid-
        flight can't fail to load 48h-old entries — ``from_cache`` simply
        ignores unknown/missing keys and the webhook re-fires. ``decision``
        is stored as its int value; ``labels`` as a sorted list.
        """
        return {
            "decision": int(self.decision),
            "is_spam_override": self.is_spam_override,
            "labels": sorted(self.labels),
            "assign_to": list(self.assign_to),
            "mark_starred": self.mark_starred,
            "mark_read": self.mark_read,
            "mark_trashed": self.mark_trashed,
            "mark_archived": self.mark_archived,
            "skip_autoreply": self.skip_autoreply,
            "events": list(self.events),
            "reply_draft_template_id": self.reply_draft_template_id,
        }

    @classmethod
    def from_cache(cls, data: Dict[str, Any]) -> "_HttpResult":
        """Rebuild from ``to_cache`` output. Tolerant of partial/old data."""
        return cls(
            decision=Decision(data.get("decision", int(Decision.CONTINUE))),
            is_spam_override=data.get("is_spam_override"),
            labels=set(data.get("labels") or []),
            assign_to=list(data.get("assign_to") or []),
            mark_starred=bool(data.get("mark_starred")),
            mark_read=bool(data.get("mark_read")),
            mark_trashed=bool(data.get("mark_trashed")),
            mark_archived=bool(data.get("mark_archived")),
            skip_autoreply=bool(data.get("skip_autoreply")),
            events=list(data.get("events") or []),
            reply_draft_template_id=data.get("reply_draft_template_id"),
        )


# --- cross-retry result cache --- #
#
# A blocking webhook runs *before* the spam step, so a step that later RETRYs
# (rspamd outage, a different blocking webhook failing) re-runs the whole
# pipeline on every 5-min sweep — re-POSTing every already-succeeded blocking
# webhook hundreds of times over the 48h window. To avoid that we memoise each
# successful blocking result in Redis and replay it on the next attempt.
#
# Deliberately lossy: the cache is only read on a retry attempt and only
# written when the task decides to RETRY (never on the happy path), and any
# miss / eviction / deploy-time schema drift simply re-fires the webhook. The
# webhook contract is already at-least-once (receivers dedupe on ``jti``), so a
# rare extra delivery is fine — we only need to turn "hundreds" into "a few".

_WEBHOOK_RESULT_CACHE_VERSION = 1
# Cover the whole deferral window so a result cached on attempt 1 is still
# served on the last retry before the message is delivered/abandoned.
_WEBHOOK_RESULT_CACHE_TTL = int(DEFERRAL_MAX_AGE.total_seconds())


def _webhook_results_cache_key(inbound_message_id: str) -> str:
    return f"inbound_webhook_results:{inbound_message_id}"


def load_cached_webhook_results(
    inbound_message_id: str,
) -> Dict[Tuple[str, str], _HttpResult]:
    """Load memoised blocking-webhook results for one inbound message.

    Returns ``{(channel_id, phase): _HttpResult}``, or ``{}`` on a miss or
    ANY deserialisation problem — the cache is a best-effort optimisation,
    never a source of truth, so every error path degrades to "re-fire".
    """
    try:
        blob = cache.get(_webhook_results_cache_key(inbound_message_id))
        if (
            not isinstance(blob, dict)
            or blob.get("version") != _WEBHOOK_RESULT_CACHE_VERSION
        ):
            return {}
        out: Dict[Tuple[str, str], _HttpResult] = {}
        for entry in blob.get("results") or []:
            channel_id = entry.get("channel")
            phase = entry.get("phase")
            if not channel_id or not phase:
                continue
            out[(str(channel_id), str(phase))] = _HttpResult.from_cache(
                entry.get("result") or {}
            )
        return out
    except Exception:  # pylint: disable=broad-exception-caught
        # Corrupt entry, schema drift across a deploy, cache backend hiccup —
        # all degrade to "re-fire", never an error on the inbound path.
        return {}


def persist_cached_webhook_results(
    inbound_message_id: str,
    results: Dict[Tuple[str, str], _HttpResult],
) -> None:
    """Persist blocking-webhook results so the next retry replays them.

    Best-effort: a write failure just means the webhooks re-fire next attempt.
    Called only when the task decides to RETRY, so the happy path never writes.
    """
    if not results:
        return
    try:
        blob = {
            "version": _WEBHOOK_RESULT_CACHE_VERSION,
            "results": [
                {"channel": channel_id, "phase": phase, "result": result.to_cache()}
                for (channel_id, phase), result in results.items()
            ],
        }
        cache.set(
            _webhook_results_cache_key(inbound_message_id),
            blob,
            timeout=_WEBHOOK_RESULT_CACHE_TTL,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Failed to persist webhook result cache for %s", inbound_message_id
        )


def _read_capped_body(response, deadline: Optional[float] = None) -> bytes:
    """Read at most ``MAX_RESPONSE_BODY`` bytes from a streaming response.

    The action body contract is tiny (a few hundred bytes). Reading
    more is wasted memory at best and a DoS vector at worst — if a
    receiver returns a huge payload we keep what we have and ignore
    the rest. Network errors mid-stream get logged and the caller
    treats the partial body as if the receiver had returned no body.

    ``deadline`` (a ``time.monotonic()`` value) bounds total read time:
    a receiver dribbling bytes just under the per-read socket timeout
    would otherwise hold the worker far past ``WEBHOOK_TIMEOUT``. When
    the deadline is crossed we raise ``TimeoutError`` so the caller
    treats it as a transport failure (RETRY), not an empty body.
    """
    chunks: List[bytes] = []
    received = 0
    try:
        for chunk in response.iter_content(chunk_size=8192, decode_unicode=False):
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("webhook response read exceeded time budget")
            if not chunk:
                continue
            remaining = MAX_RESPONSE_BODY - received
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                received = MAX_RESPONSE_BODY
                break
            chunks.append(chunk)
            received += len(chunk)
    except Exception as exc:
        # TimeoutError MUST propagate so the caller treats it as RETRY.
        # Everything else (network blip mid-stream) is logged silently —
        # the body we have so far (possibly empty) is returned and the
        # caller classifies it as a benign empty-body CONTINUE.
        if isinstance(exc, TimeoutError):
            raise
        # Don't interpolate ``exc`` — its text can echo the request URL or
        # body and leak receiver secrets into logs. The type name is enough.
        logger.warning("Truncated response body read failed (%s)", type(exc).__name__)
    return b"".join(chunks)


def _sanitize_url(url: str) -> str:
    """Reduce a webhook URL to ``scheme://host[:port]`` for safe logging.

    Receivers routinely embed a secret token in the path, query string
    or userinfo (e.g. ``https://hook.example.com/in/<token>``); logging
    the raw URL would leak it. We keep only the scheme, host and port —
    enough to identify the receiver without exposing credentials.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return "<unparseable-url>"
    if not parsed.hostname:
        return "<no-host>"
    host = parsed.hostname
    try:
        # ``.port`` re-parses the netloc and raises ValueError on a malformed
        # port even though ``urlparse`` itself succeeded — swallow it and log
        # host-only rather than let a bad URL crash the logging path.
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def _failure(blocking: bool, decision: Decision) -> _HttpResult:
    """Failure-path result: blocking → propagate ``decision`` (DROP /
    RETRY); non-blocking → CONTINUE (fire-and-forget, never stalls
    delivery)."""
    return _HttpResult(decision=decision if blocking else Decision.CONTINUE)


def _config_skip() -> _HttpResult:
    """Result for a misconfiguration that waiting can't fix: a missing
    secret / url / auth_method, or a URL the SSRF guard refuses at dispatch
    time. Create/update validation rejects internal or unresolvable URLs and
    guarantees a secret + valid auth_method, so at dispatch these mean either
    a hand-edited (non-DRF) row or a malicious DNS rebinding — none of which a
    48h retry would ever clear.

    So CONTINUE: deliver the mail past the broken webhook rather than stall a
    whole scope's inbound (instance-wide for a GLOBAL blocking webhook) for up
    to 48 hours waiting for a config that has never worked. The SSRF guard
    itself is unchanged — ``SSRFSafeSession`` still refuses to POST to an
    internal address — so only this one webhook's gatekeeping is skipped (the
    rest of the pipeline, incl. rspamd, still runs). Every caller logs at
    ERROR so an admin is paged to fix or disable the channel. (A future
    ``channel.is_active`` will let an operator disable it outright.)

    NB this is fail-OPEN for that webhook: a hard spam/security gate is
    bypassed during the failure. Acceptable because it's a rare edge, the
    breakage is loud, and stalling all inbound is the worse outcome — see
    docs/webhooks.md."""
    return _HttpResult()  # Decision.CONTINUE


def _classify_response_body(body_bytes: bytes) -> _HttpResult:
    """Parse a 2xx response body into an ``_HttpResult``.

    Empty body or non-JSON body → plain CONTINUE.

    JSON shape (all keys optional):
      - ``action``: ``"drop"`` short-circuits delivery; anything else
        (``"accept"``, missing, or an unknown value) → CONTINUE. A 2xx is
        a *successful* response, so it can't ask to be retried — a receiver
        that needs redelivery returns a non-2xx status (e.g. 429/503),
        handled as a transient failure → RETRY.
      - ``is_spam``: bool; overrides the pipeline's spam verdict.
      - ``add_labels``: list of label UUID strings; the pipeline
        validates them against the destination mailbox.
    """
    if not body_bytes:
        return _HttpResult()
    try:
        # ``json.loads`` accepts bytes natively and raises ValueError
        # (incl. JSONDecodeError) on anything malformed. Deeply-nested
        # JSON raises RecursionError (a RuntimeError, not a ValueError),
        # so catch it too — a misbehaving receiver must never escape this
        # parser and stall the message on an uncaught exception.
        payload = json.loads(body_bytes)
    except (ValueError, RecursionError):
        return _HttpResult()
    if not isinstance(payload, dict):
        return _HttpResult()

    result = _HttpResult()

    action = payload.get("action")
    if isinstance(action, str) and action.lower() == "drop":
        # ``drop`` is the only receiver-chosen decision on a 2xx: discard the
        # mail. There is deliberately no body-driven "retry" — a successful
        # (2xx) response can't ask to be retried; redelivery is signalled by
        # a non-2xx status (handled as a transient failure → RETRY).
        result.decision = Decision.DROP

    is_spam = payload.get("is_spam")
    if isinstance(is_spam, bool):
        result.is_spam_override = is_spam

    labels = payload.get("add_labels")
    if isinstance(labels, list):
        for item in labels[:MAX_LABELS_PER_RESPONSE]:
            if not isinstance(item, str):
                continue
            try:
                # Normalise to canonical UUID string; rejects garbage
                # before it ever hits the DB.
                result.labels.add(str(uuid_module.UUID(item)))
            except ValueError:
                continue

    assign_to = payload.get("assign_to")
    if isinstance(assign_to, list):
        # Receiver-supplied OIDC emails. Light filter only: must be a
        # non-empty string containing '@'. Lowercased + deduped while
        # preserving order so a multi-email payload assigns in a
        # predictable sequence. The pipeline does the real work
        # (resolve to User, check editor rights, skip ambiguous).
        seen: Set[str] = set()
        for item in assign_to[:MAX_ASSIGN_TO_PER_RESPONSE]:
            if not isinstance(item, str):
                continue
            email = item.strip().lower()
            if not email or "@" not in email or email in seen:
                continue
            seen.add(email)
            result.assign_to.append(email)

    # Bool flags. ``true``-only semantics — see ``_HttpResult``.
    for key in (
        "mark_starred",
        "mark_read",
        "mark_trashed",
        "mark_archived",
        "skip_autoreply",
    ):
        if payload.get(key) is True:
            setattr(result, key, True)

    reply_draft = payload.get("reply_draft")
    if isinstance(reply_draft, dict):
        candidate = reply_draft.get("template")
        if isinstance(candidate, str):
            try:
                # Normalise to canonical UUID; rejects garbage before
                # the DB lookup.
                result.reply_draft_template_id = str(uuid_module.UUID(candidate))
            except ValueError:
                pass

    add_event = payload.get("add_event")
    if isinstance(add_event, list):
        for item in add_event[:MAX_EVENTS_PER_RESPONSE]:
            if not isinstance(item, dict):
                continue
            event_type = item.get("type")
            if event_type == "im":
                content = item.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                # Cap per-comment size — the comment is stored in
                # ``ThreadEvent.data`` JSONB on every inbound, and we
                # don't want a misconfigured receiver to flood the
                # timeline with 60KB blobs.
                if len(content.encode("utf-8")) > MAX_IM_CONTENT_BYTES:
                    content = content.encode("utf-8")[:MAX_IM_CONTENT_BYTES].decode(
                        "utf-8", errors="ignore"
                    )
                # Mirror the existing IM ThreadEvent shape so the
                # pipeline can persist verbatim. ``mentions`` is
                # intentionally empty for webhook-driven IMs: receivers
                # don't know user UUIDs upfront and we don't want
                # email-based mentions sneaking in here without the
                # mention-notification semantics being designed.
                result.events.append({"type": "im", "content": content, "mentions": []})
            # Unknown event types (incl. future ``type=iframe``) are
            # silently dropped here — the classifier doesn't know how
            # to validate them yet. Forward-compatible: new types
            # become live the moment the classifier learns them, with
            # no contract change for receivers that already emit them.

    return result


FORMAT_EML = "eml"
FORMAT_JMAP = "jmap"
# ``jmap_metadata`` is the cheap notification variant: same JMAP
# envelope (headers, from/to/subject, messageId, etc.) but no body
# parts, no bodyValues, no attachments. Receivers that only need the
# "a message arrived" signal can use it without ever seeing the body
# content over the wire.
FORMAT_JMAP_METADATA = "jmap_metadata"
VALID_FORMATS = frozenset({FORMAT_EML, FORMAT_JMAP, FORMAT_JMAP_METADATA})
DEFAULT_FORMAT = FORMAT_EML

USER_AGENT = "Messages-Webhook/1.0"

# ``auth_method=jwt``: a short-lived HS256 JWT in the Authorization header.
# It is HMAC-based (HS256), binds the exact request body via the
# ``body_sha256`` claim, and carries ``exp`` (replay window) + ``jti``
# (nonce) — a complete, self-contained signature a receiver verifies with
# any standard JWT library and the channel secret. (A separate raw-HMAC
# scheme, if ever wanted, would be its own ``auth_method`` — not bolted on
# here; the JWT already subsumes it.)
JWT_ISSUER = "messages-webhook"
JWT_TTL_SECONDS = 300  # 5 min — receivers SHOULD reject older tokens (exp)


def _resolve_body(
    body_format: str,
    raw_data: bytes,
    parsed_email: JmapEmail,
    *,
    message_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Tuple[str, bytes]:
    """Compute (Content-Type, raw bytes to sign and POST).

    The dispatcher needs the Content-Type to send and the exact byte
    string the signature is computed over — which is also the byte
    string we POST verbatim via ``data=``.

    JSON is serialised here once so the signature and the wire bytes
    cannot drift (``requests`` would otherwise re-serialise with
    different separators/ordering).

    ``message_id`` / ``thread_id`` are passed only on the post-creation
    (``message.delivered``) path, where they populate the JMAP body's
    ``id`` / ``threadId``; the blocking paths fire before the row exists
    and omit them (see ``build_jmap_email``).
    """
    if body_format == FORMAT_EML:
        return "message/rfc822", raw_data
    include_body = body_format == FORMAT_JMAP
    payload = build_jmap_email(
        parsed_email,
        include_body=include_body,
        message_id=message_id,
        thread_id=thread_id,
    )
    # ``separators=(",", ":")`` produces the compact bytes we sign.
    # Hand the same bytes to ``requests`` via ``data=`` so what we sign
    # is exactly what we POST.
    body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return "application/json", body_bytes


def _sign_jwt(
    secret: str,
    *,
    channel: models.Channel,
    mailbox: models.Mailbox,
    body_bytes: bytes,
    issued_at: int,
) -> str:
    """Build an HS256 JWT for ``Authorization: Bearer …``.

    Claims:
      - ``iss`` — fixed string so receivers can pin issuer.
      - ``iat`` / ``exp`` — short TTL, prevents replay.
      - ``jti`` — random nonce, for receivers that dedupe replays
        beyond timestamp checks.
      - ``sub`` — the destination mailbox (informational).
      - ``cid`` — channel id (matches ``X-StMsg-Channel-Id``).
      - ``body_sha256`` — hex SHA-256 of the request body. Lets the
        receiver bind the JWT to the exact bytes posted, rather than
        trusting transport.

    Encoded with HS256 using the channel's shared secret — receivers
    verify with the same key.
    """
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    claims = {
        "iss": JWT_ISSUER,
        "iat": issued_at,
        "exp": issued_at + JWT_TTL_SECONDS,
        "jti": secrets.token_urlsafe(16),
        "sub": str(mailbox),
        "cid": str(channel.id),
        "body_sha256": body_hash,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


# --- channel lookup --- #


def find_webhook_channels_for_mailbox(
    mailbox: models.Mailbox,
) -> List[models.Channel]:
    """Return every webhook Channel that fires for ``mailbox``.

    Includes:
      - mailbox-scoped channels bound to this mailbox
      - maildomain-scoped channels bound to this mailbox's domain
      - global channels (instance-wide; admin/CLI-only to create)

    Phase filtering is done by the caller because the same channel set is
    read twice (before- and after-spam).
    """
    return list(
        models.Channel.objects.filter(
            Q(type=enums.ChannelTypes.WEBHOOK)
            & (
                Q(
                    scope_level=enums.ChannelScopeLevel.MAILBOX,
                    mailbox=mailbox,
                )
                | Q(
                    scope_level=enums.ChannelScopeLevel.MAILDOMAIN,
                    maildomain=mailbox.domain,
                )
                | Q(scope_level=enums.ChannelScopeLevel.GLOBAL)
            )
        )
    )


# --- envelope headers --- #


def _envelope_headers(
    *,
    channel: models.Channel,
    mailbox: models.Mailbox,
    recipient_email: str,
    is_spam: Optional[bool],
    message: Optional[models.Message] = None,
) -> Dict[str, str]:
    """Build the ``X-StMsg-*`` envelope headers attached to every webhook
    POST regardless of body format. Same shape for ``eml`` and ``jmap``.

    The firing lifecycle event is sent as ``X-StMsg-Trigger`` (e.g.
    ``message.delivered``) — a single header that already says what
    happened and, implicitly, how (``message.inbound``/``message.delivering``
    are blocking, ``message.delivered`` is fire-and-forget). No separate
    event/phase/mode headers: they'd only duplicate it, and
    ``X-StMsg-Is-Spam: pending`` already marks a before-spam firing.

    The *MIME* message-id is intentionally *not* a header: every body
    format already carries it (``messageId`` in the jmap variants, the
    raw ``Message-ID:`` header in ``eml``), so a header would only
    duplicate it.

    When ``message`` is supplied (the non-blocking path fires after the
    ``Message`` is persisted) we add the platform's own ``Message`` /
    ``Thread`` ids so a receiver can call back into our API. Blocking
    webhooks fire before the row exists, so they don't carry them.
    """
    if is_spam is None:
        # No verdict yet — the spam check hasn't run (a ``message.inbound``
        # webhook fires before it). "pending" says that plainly.
        spam_value = "pending"
    else:
        spam_value = "true" if is_spam else "false"
    headers = {
        "User-Agent": USER_AGENT,
        "X-StMsg-Trigger": (channel.settings or {}).get("trigger", ""),
        "X-StMsg-Channel-Id": str(channel.id),
        "X-StMsg-Mailbox": str(mailbox),
        "X-StMsg-Mailbox-Id": str(mailbox.id),
        "X-StMsg-Recipient": recipient_email,
        "X-StMsg-Is-Spam": spam_value,
    }
    # The instance's own public URL, so a receiver (especially a shared one
    # serving several instances) knows who fired and can turn the ``*-Id``
    # headers into callback API URLs. Optional — omitted when unconfigured.
    if settings.INSTANCE_URL:
        headers["X-StMsg-Instance"] = settings.INSTANCE_URL
    if message is not None:
        headers["X-StMsg-Message-Id"] = str(message.id)
        headers["X-StMsg-Thread-Id"] = str(message.thread_id)
    return headers


# --- dispatch --- #


class UserWebhookStep:
    """Pipeline ``Step`` wrapping one webhook ``Channel``.

    Each matching channel becomes its own step in the inbound pipeline
    (one per phase). On call, the step POSTs the configured body to
    the channel URL, classifies the response, applies any
    ``is_spam`` override and ``labels`` to ``ctx``, and returns a
    ``Decision``:

      - non-blocking → always ``CONTINUE`` (fire-and-forget; failures
        only logged)
      - blocking:
          * 2xx + ``{"action":"drop"}`` → DROP (the *only* path to DROP)
          * 2xx + anything else → CONTINUE (with optional side effects)
          * TRANSIENT failure — any non-2xx (4xx / 5xx / 3xx), timeout,
            connection, generic transport, response-read budget → RETRY
            (recoverable; bounded by the 48h deferral window)
          * CONFIG failure — SSRF reject / missing secret / url /
            auth_method → CONTINUE past the broken webhook + ``log.error``
            (retry can't fix it; see ``_config_skip``)

    A webhook error never drops the user's email — only an explicit
    ``{"action": "drop"}`` does. A *transient* failure is held for retry
    (bounded by the 48h deferral window); a *config* failure delivers
    the mail past the broken webhook and pages an admin.
    """

    def __init__(self, channel: models.Channel, phase: str):
        self.channel = channel
        self.phase = phase
        # Phase suffix in the name lets the task return value carry
        # "which phase did this drop happen at" without a separate field.
        self.name = f"webhook[{channel.id}]:{phase}"

    def __call__(self, ctx: InboundContext) -> Decision:
        cfg = self.channel.settings or {}
        # Only message.delivered is fire-and-forget; the others block and run
        # inline (see docs/webhooks.md). Unknown/missing → non-blocking
        # (fail-closed; the step is only built for a valid trigger anyway).
        blocking = cfg.get("trigger") in (
            enums.WebhookTrigger.MESSAGE_INBOUND,
            enums.WebhookTrigger.MESSAGE_DELIVERING,
        )

        if not blocking:
            # Non-blocking (``message.delivered``) webhooks can't influence
            # delivery, so they don't run network I/O on the inbound worker.
            # We also don't render or snapshot the body here: we record the
            # channel with the spam verdict at this point and fire it AFTER
            # the Message is created — see ``dispatch_recorded_webhooks``.
            # The task then renders the payload from the durable
            # ``Message.blob``, so the email bytes never get copied or pushed
            # through the broker. ``message.delivered`` always runs after the
            # spam step, so the recorded verdict is the final one.
            #
            # Consequence (intended): a non-blocking webhook fires only for
            # messages that actually become a Message — not for ones a
            # blocking webhook later DROPs.
            ctx.pending_webhooks.append((self.channel.id, ctx.is_spam))
            return Decision.CONTINUE

        # Replay a memoised result from a previous attempt instead of
        # re-POSTing (see the cross-retry result cache above). The map is only
        # pre-loaded on a retry attempt, so on the happy path this is always a
        # miss and we fire normally.
        cache_key = (str(self.channel.id), self.phase)
        result = ctx.blocking_webhook_results.get(cache_key)
        if result is None:
            body_format = cfg.get("format", DEFAULT_FORMAT)
            content_type, body_bytes = _resolve_body(
                body_format, ctx.raw_data, ctx.parsed_email
            )
            result = _dispatch_webhook(
                channel=self.channel,
                mailbox=ctx.mailbox,
                is_spam=ctx.is_spam,
                recipient_email=ctx.recipient_email,
                content_type=content_type,
                body_bytes=body_bytes,
                blocking=True,
            )
            # Memoise only terminal (non-RETRY) outcomes — a webhook that
            # failed must re-fire next attempt, not be served from cache. The
            # task persists this map to Redis iff it ends up RETRYing.
            if result.decision != Decision.RETRY:
                ctx.blocking_webhook_results[cache_key] = result
        if result.is_spam_override is not None:
            ctx.is_spam = result.is_spam_override
        ctx.labels |= result.labels
        if result.assign_to:
            # Defer the actual assignment until after the thread
            # exists (post-message-creation). Each blocking webhook
            # that asked gets its own ThreadEvent ASSIGN, attributed
            # to this channel.
            ctx.pending_assigns.append((self.channel.id, result.assign_to))
        # Bool flags OR-merge: any blocking webhook saying true sticks.
        ctx.mark_starred = ctx.mark_starred or result.mark_starred
        ctx.mark_read = ctx.mark_read or result.mark_read
        ctx.mark_trashed = ctx.mark_trashed or result.mark_trashed
        ctx.mark_archived = ctx.mark_archived or result.mark_archived
        ctx.skip_autoreply = ctx.skip_autoreply or result.skip_autoreply
        for event in result.events:
            # Per-event attribution like assigns — one ThreadEvent per
            # add_event entry, with channel set to the firing webhook.
            ctx.pending_events.append((self.channel.id, event))
        if result.reply_draft_template_id:
            # Defer template lookup + draft creation until after the
            # message + thread land. Each blocking webhook that asked
            # produces its own draft, channel-attributed.
            ctx.pending_drafts.append((self.channel.id, result.reply_draft_template_id))
        return result.decision


def _build_auth_headers(
    channel: models.Channel,
    secret: str,
    body_bytes: bytes,
    mailbox: models.Mailbox,
) -> Optional[Dict[str, str]]:
    """Return the auth headers for the channel's ``auth_method``, or
    ``None`` when the channel is misconfigured (caller fails closed)."""
    auth_method = (channel.settings or {}).get("auth_method")

    if auth_method == enums.WebhookAuthMethod.API_KEY:
        # Static key derived from the root via HMAC, presented as an opaque
        # Bearer token (RFC 6750 — a Bearer token need not be a JWT). Sent via
        # ``Authorization`` like the jwt method, so logging / proxy / APM
        # tooling auto-redacts it — which matters for a long-lived static
        # credential. The raw root never touches the wire; a receiver-side leak
        # of this derived value reveals nothing about the root.
        return {"Authorization": f"Bearer {channel.get_webhook_api_key()}"}

    if auth_method == enums.WebhookAuthMethod.JWT:
        # Short-TTL HS256 JWT keyed by the root secret, binding the exact
        # body via ``body_sha256``. Signed at send time (here / in the task),
        # so the TTL is measured from the actual POST, not enqueue.
        bearer = _sign_jwt(
            secret,
            channel=channel,
            mailbox=mailbox,
            body_bytes=body_bytes,
            issued_at=int(time.time()),
        )
        return {"Authorization": f"Bearer {bearer}"}

    # Settings validator forbids creating a webhook channel without a
    # valid auth_method; an existing row with a missing/unknown value is
    # misconfigured.
    logger.warning(
        "Webhook channel %s has missing/unknown auth_method=%r — skipping",
        channel.id,
        auth_method,
    )
    return None


def _deliver_signed_webhook(
    *,
    channel: models.Channel,
    mailbox: models.Mailbox,
    url: str,
    content_type: str,
    body_bytes: bytes,
    envelope_headers: Dict[str, str],
    blocking: bool,
) -> _HttpResult:
    """Sign and POST one webhook, returning the classified ``_HttpResult``.

    The single network path shared by the inline blocking step and the
    out-of-band non-blocking task, so signing/SSRF/timeout/response
    handling can never drift between them.
    """
    secret = (channel.encrypted_settings or {}).get("secret")
    if not secret:
        # The create path always mints a secret; a row without one is a
        # (non-DRF) misconfiguration that retry can't fix — CONTINUE past it
        # rather than stall the scope's inbound (see ``_config_skip``).
        logger.error(
            "Webhook channel %s has no secret — delivering past it; "
            "fix or disable the channel",
            channel.id,
        )
        return _config_skip()

    auth_headers = _build_auth_headers(channel, secret, body_bytes, mailbox)
    if auth_headers is None:
        # Unknown/missing auth_method. Create-time validation requires a valid
        # one, so this is a non-DRF row; retry can't fix it — CONTINUE past it.
        # ``_build_auth_headers`` logged the detail; surface it at ERROR here.
        logger.error(
            "Webhook channel %s has an unusable auth_method — delivering past "
            "it; fix or disable the channel",
            channel.id,
        )
        return _config_skip()

    signed_headers = {
        **envelope_headers,
        "Content-Type": content_type,
        **auth_headers,
    }
    # ``stream=True`` lets us cap the response body we actually read — a
    # misconfigured receiver returning a multi-GB error page must not OOM
    # the worker. The ``(connect, read)`` tuple bounds the connect phase
    # tightly and each socket read; the ``deadline`` below bounds the
    # *total* exchange against slow drip.
    deadline = time.monotonic() + WEBHOOK_TIMEOUT
    try:
        response = SSRFSafeSession().post(
            url,
            timeout=(WEBHOOK_CONNECT_TIMEOUT, WEBHOOK_TIMEOUT),
            stream=True,
            headers=signed_headers,
            data=body_bytes,
        )
    except SSRFValidationError as exc:
        # The URL resolves to a disallowed (internal) address or won't
        # resolve. Create/update validation already rejects internal /
        # unresolvable URLs, so at dispatch this is a DNS rebinding (or a
        # non-DRF row) — retry can't fix it, and the guard already (correctly)
        # refused to POST to the internal target. CONTINUE past it rather than
        # stall inbound. (``exc`` carries only the hostname, never the
        # secret-bearing path/query, so it's safe to log.)
        logger.error(
            "Webhook channel %s rejected by SSRF for url=%s: %s — delivering "
            "past it; fix or disable the channel",
            channel.id,
            _sanitize_url(url),
            exc,
        )
        return _config_skip()
    except Exception as exc:
        # Timeout, connection refused, DNS, unknown transport-level
        # failure: all transient. The 48-hour deferral window in the
        # pipeline runner bounds the retries. Log only the exception
        # *type*, not its message or traceback: requests/urllib3 errors
        # embed the full request URL (path + query), which is exactly
        # where receivers carry secret tokens, so ``exc``/``exc_info``
        # would bypass ``_sanitize_url``.
        logger.warning(
            "Webhook channel %s network error (%s) for url=%s",
            channel.id,
            type(exc).__name__,
            _sanitize_url(url),
        )
        return _failure(blocking, Decision.RETRY)

    try:
        status = response.status_code
        if 200 <= status < 300:
            if not blocking:
                # Non-blocking webhooks never influence delivery — ignore
                # the body entirely. Avoids surprises if a receiver
                # accidentally returns {"action":"drop"}.
                return _HttpResult()
            try:
                body_bytes_response = _read_capped_body(response, deadline=deadline)
            except TimeoutError:
                logger.warning(
                    "Webhook channel %s exceeded %ss budget reading response "
                    "for url=%s — holding for retry",
                    channel.id,
                    WEBHOOK_TIMEOUT,
                    _sanitize_url(url),
                )
                return _failure(blocking, Decision.RETRY)
            result = _classify_response_body(body_bytes_response)
            if result.decision == Decision.DROP:
                logger.info(
                    "Webhook channel %s requested DROP via response body for url=%s",
                    channel.id,
                    _sanitize_url(url),
                )
            return result

        logger.info(
            "Webhook channel %s returned status %s for url=%s",
            channel.id,
            status,
            _sanitize_url(url),
        )
        # Any non-2xx status is a transient failure → RETRY. A blocking
        # webhook DROPs an email *only* when it explicitly returns
        # ``{"action": "drop"}`` with a 2xx (handled above). A receiver
        # bug that answers 4xx must never cost the user their mail — the
        # 48-hour deferral window bounds the hold.
        return _failure(blocking, Decision.RETRY)
    finally:
        response.close()


def _dispatch_webhook(
    *,
    channel: models.Channel,
    mailbox: models.Mailbox,
    is_spam: Optional[bool],
    recipient_email: str,
    content_type: str,
    body_bytes: bytes,
    blocking: bool,
    message: Optional[models.Message] = None,
) -> _HttpResult:
    """Build the envelope headers and deliver one webhook.

    The shared entry point above ``_deliver_signed_webhook``: both the
    inline blocking step and the out-of-band non-blocking task land here,
    so the URL lookup and header-building can't drift between them.
    ``message`` is set on the non-blocking path (fired post-persist) so
    its id / thread id ride along as headers.
    """
    url = (channel.settings or {}).get("url")
    if not url:
        # The serializer guarantees a url on create; a row without one is a
        # (non-DRF) misconfiguration retry can't fix — CONTINUE past it rather
        # than stall inbound (see ``_config_skip``).
        logger.error(
            "Webhook channel %s has no url — delivering past it; "
            "fix or disable the channel",
            channel.id,
        )
        return _config_skip()
    envelope_headers = _envelope_headers(
        channel=channel,
        mailbox=mailbox,
        recipient_email=recipient_email,
        is_spam=is_spam,
        message=message,
    )
    return _deliver_signed_webhook(
        channel=channel,
        mailbox=mailbox,
        url=url,
        content_type=content_type,
        body_bytes=body_bytes,
        envelope_headers=envelope_headers,
        blocking=blocking,
    )


def _resolve_body_from_message(
    body_format: str, message: models.Message
) -> Tuple[str, bytes]:
    """Render the webhook body from a durable ``Message``.

    The non-blocking dispatch path sources its bytes from the stored
    ``Message.blob`` (re-parsed the same way the pipeline parsed them)
    instead of a transient snapshot, so there's no second copy of the
    email. Mirrors ``_resolve_body``'s output contract.

    Future optimization: with K non-blocking webhooks on one message this
    re-fetches and re-parses the same blob K times (one per task). A
    short-lived blob/parse cache keyed by ``blob_id`` would let a single
    message's fan-out fetch + parse once. See "Performance notes" in
    docs/webhooks.md.
    """
    raw_data = message.blob.get_content()
    parsed_email = parse_email(raw_data)
    if parsed_email is None and body_format != FORMAT_EML:
        # The stored MIME can't be re-parsed, so we can't render a faithful
        # JMAP body. Fail loudly (the caller logs and the dispatch is
        # treated as a failure) rather than POST a near-empty Email that
        # silently drops the message's identity and content. The EML format
        # is exempt: it ships the raw bytes verbatim and ignores the parse.
        raise ValueError(
            f"cannot parse stored blob for message {message.id} into "
            f"webhook format {body_format!r}"
        )
    # Post-creation path: stamp the persisted ids into the JMAP body.
    return _resolve_body(
        body_format,
        raw_data,
        parsed_email or {},
        message_id=str(message.id),
        thread_id=str(message.thread_id),
    )


def dispatch_recorded_webhooks(
    message: models.Message,
    mailbox: models.Mailbox,
    pending: List[Tuple[Any, Optional[bool]]],
) -> None:
    """Fire the non-blocking webhooks recorded during the pipeline.

    Called from the inbound finalizer once the ``Message`` exists and is
    committed (the inbound task runs in autocommit). Each task receives
    only ids — it re-fetches the message and renders the body from
    ``Message.blob`` at run time, so nothing large rides the broker and
    there's no payload snapshot to keep alive. If the message somehow
    isn't there when the task runs, the task skips rather than guessing.
    """
    if not pending:
        return
    message_id = str(message.id)
    mailbox_id = str(mailbox.id)
    for channel_id, is_spam in pending:
        dispatch_webhook_task.delay(message_id, str(channel_id), mailbox_id, is_spam)


@celery_app.task
def dispatch_webhook_task(
    message_id: str,
    channel_id: str,
    mailbox_id: str,
    is_spam: Optional[bool],
) -> None:
    """Deliver one non-blocking webhook off the inbound worker.

    Non-blocking webhooks can't influence delivery, so their network I/O
    runs here (default queue) instead of pinning the time-sensitive
    inbound pipeline worker. Best-effort and at-least-once: the message
    is already handled, so any failure is logged and swallowed (a
    non-blocking webhook never affects delivery). The request is re-signed
    here at send time, so the root secret never travels through the
    broker and the JWT TTL is measured from the actual POST.

    The payload never travels through the broker — only the ``message_id``
    does. We re-fetch (and so re-validate) the source ``Message`` at task
    init and render the body from its blob; if the message is already gone
    (e.g. deleted before the task ran) the dispatch is skipped rather than
    guessed at.
    """
    try:
        channel = models.Channel.objects.filter(id=channel_id).first()
        mailbox = models.Mailbox.objects.filter(id=mailbox_id).first()
        if channel is None or mailbox is None:
            return
        # Re-validate the channel at send time: it may have been retyped,
        # re-triggered, or re-scoped between the pipeline recording it and this
        # task running. Skip (rather than post) if it no longer applies. The
        # scope-matcher already filters to type=webhook channels covering this
        # mailbox, so channel membership subsumes the type + mailbox checks.
        applicable = {c.id for c in find_webhook_channels_for_mailbox(mailbox)}
        trigger = (channel.settings or {}).get("trigger")
        if (
            channel.id not in applicable
            or trigger != enums.WebhookTrigger.MESSAGE_DELIVERED
        ):
            logger.warning(
                "Webhook channel %s no longer applies to mailbox %s "
                "(trigger=%r) — skipping dispatch",
                channel_id,
                mailbox_id,
                trigger,
            )
            return
        message = models.Message.objects.filter(id=message_id).first()
        if message is None or message.blob_id is None:
            logger.warning(
                "Webhook source message %s missing — skipping dispatch (channel=%s)",
                message_id,
                channel_id,
            )
            return
        # Confirm the message actually belongs to this mailbox before posting
        # its body: a stale/mismatched task must not leak another mailbox's
        # content. A Message links to a mailbox only via its thread's
        # ThreadAccess rows, so check for one covering ``mailbox_id``.
        if not message.thread.accesses.filter(mailbox_id=mailbox_id).exists():
            logger.warning(
                "Webhook source message %s does not belong to mailbox %s — "
                "skipping dispatch (channel=%s)",
                message_id,
                mailbox_id,
                channel_id,
            )
            return
        body_format = (channel.settings or {}).get("format", DEFAULT_FORMAT)
        content_type, body_bytes = _resolve_body_from_message(body_format, message)
        _dispatch_webhook(
            channel=channel,
            mailbox=mailbox,
            is_spam=is_spam,
            recipient_email=str(mailbox),
            content_type=content_type,
            body_bytes=body_bytes,
            blocking=False,
            message=message,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Non-blocking webhook dispatch failed (channel=%s)", channel_id
        )


def webhook_steps_for_mailbox(
    mailbox: models.Mailbox,
    *,
    phase: str,
    channels: Optional[List[models.Channel]] = None,
) -> List[Step]:
    """Build one ``UserWebhookStep`` per matching channel for the phase.

    Channels are filtered here (the trigger's phase, url present, valid
    format) rather than at run time so the pipeline iterator sees a flat
    list of ready-to-call steps. A channel runs at exactly the phase its
    ``trigger`` maps to (``message.delivered`` and ``message.delivering``
    → after-spam; ``message.inbound`` → before-spam).

    ``channels`` may be passed in to reuse a single channel-set query
    across both phases (the set is identical before- and after-spam);
    when omitted it is fetched here.
    """
    if phase not in VALID_PHASES:
        raise ValueError(f"Invalid webhook phase: {phase}")

    if channels is None:
        channels = find_webhook_channels_for_mailbox(mailbox)

    steps: List[Step] = []
    for channel in channels:
        cfg = channel.settings or {}
        trigger = cfg.get("trigger")
        if trigger not in enums.WebhookTrigger:
            # Misconfigured/legacy row (or a future, non-inbound event).
            # Fail closed.
            logger.warning(
                "Webhook channel %s has unknown trigger=%r — skipping",
                channel.id,
                trigger,
            )
            continue
        # Only message.inbound fires before the spam check (docs/webhooks.md).
        runs_at = (
            PHASE_BEFORE_SPAM
            if trigger == enums.WebhookTrigger.MESSAGE_INBOUND
            else PHASE_AFTER_SPAM
        )
        if runs_at != phase:
            continue
        if not cfg.get("url"):
            continue
        body_format = cfg.get("format", DEFAULT_FORMAT)
        if body_format not in VALID_FORMATS:
            # Serializer should have caught this on write — fail
            # closed rather than POST in a shape the receiver wasn't
            # promised.
            logger.warning(
                "Webhook channel %s has invalid format=%r — skipping",
                channel.id,
                body_format,
            )
            continue
        steps.append(UserWebhookStep(channel, phase=phase))

    # Within the after-spam phase, blocking triggers (message.delivering)
    # must run before non-blocking ones (message.delivered) so the latter
    # records the final ``ctx.is_spam`` — a delivering webhook that
    # overrides the verdict after a delivered webhook has already captured
    # it would leave the non-blocking dispatch with a stale value.
    if phase == PHASE_AFTER_SPAM:
        steps.sort(
            key=lambda s: (
                s.channel.settings.get("trigger")
                == enums.WebhookTrigger.MESSAGE_DELIVERING
            ),
            reverse=True,
        )
    return steps

"""JMAP payload builders for outbound webhooks.

Projects the parsed JMAP ``Email`` object (RFC 8621 §4.1) into the
webhook wire payload. Kept separate from ``dispatch_webhooks`` (the HTTP
plumbing / signing / dispatch glue) because it's a pure data
transformation with no network or model dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from jmap_email import JmapEmail


def _utcdate(value: Any) -> Optional[str]:
    """Format a datetime as a JMAP ``UTCDate`` (RFC 3339 with ``Z`` suffix).

    Falls back to the raw value if it isn't a datetime — the parser may
    have given us a pre-formatted string. ``None`` stays ``None``.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def _strip_body_part(part: Dict[str, Any]) -> Dict[str, Any]:
    """A JMAP ``EmailBodyPart`` without our parser's project extensions.

    ``content`` (raw bytes on attachment parts) and ``sha256`` are
    parser extensions, NOT RFC 8621. Attachment bytes in particular are
    never embedded in the JSON body — JMAP keeps them behind a
    ``blobId`` (which we don't have at webhook-fire time), and raw bytes
    aren't JSON-serialisable anyway. Receivers that need the bytes use
    ``format=eml``.
    """
    return {k: v for k, v in part.items() if k not in ("content", "sha256")}


def build_jmap_email(
    parsed_email: JmapEmail,
    *,
    include_body: bool = True,
    message_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Project the parsed JMAP ``Email`` object into the webhook payload.

    ``parse_email`` already returns a strict JMAP Email object
    (RFC 8621 §4.1), so this is mostly a copy. We stamp ``receivedAt``
    (the moment the webhook fires) and strip the parser's project
    extensions (``_ext`` and per-part ``content`` / ``sha256``) so the
    body is strict JMAP.

    Storage-time fields are populated only when the persisted ``Message``
    exists — i.e. the non-blocking ``message.delivered`` path, which fires
    after creation and passes ``message_id`` / ``thread_id`` (also sent as
    ``X-StMsg-Message-Id`` / ``X-StMsg-Thread-Id`` headers). The blocking
    ``message.inbound`` / ``message.delivering`` paths fire *before* the row
    exists, pass neither, and so omit them. ``blobId`` / ``mailboxIds`` /
    ``keywords`` stay absent everywhere: they'd need a JMAP blob endpoint we
    don't expose and a folder/flag mapping we haven't designed.

    With ``include_body=False`` the body parts, ``bodyValues`` and
    ``attachments`` are dropped — receivers get a notification-only
    payload (subject + envelope addresses + headers) without the
    message body content ever leaving the instance over the wire.
    ``hasAttachment`` is preserved so receivers can still tell whether
    the message had any.
    """
    email: Dict[str, Any] = dict(parsed_email)
    email.pop("_ext", None)  # project extension, not strict JMAP
    email["receivedAt"] = _utcdate(datetime.now(timezone.utc))
    # Present only on the post-creation (``message.delivered``) path.
    if message_id is not None:
        email["id"] = message_id
    if thread_id is not None:
        email["threadId"] = thread_id

    if include_body:
        email["textBody"] = [
            _strip_body_part(p) for p in parsed_email.get("textBody") or []
        ]
        email["htmlBody"] = [
            _strip_body_part(p) for p in parsed_email.get("htmlBody") or []
        ]
        email["attachments"] = [
            _strip_body_part(p) for p in parsed_email.get("attachments") or []
        ]
    else:
        for key in (
            "textBody",
            "htmlBody",
            "bodyValues",
            "bodyStructure",
            "attachments",
            "preview",
        ):
            email.pop(key, None)

    return email

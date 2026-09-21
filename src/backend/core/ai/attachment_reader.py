"""Read the citizen's attachments so the AI reply can take them into account.

Plain text files are decoded directly; PDF, images and Word documents go
through the OCR model of the AI service. Every attachment ends up in the
prompt, either with its text or with the reason it could not be read, so the
model never pretends to have read a document it did not see.
"""

import dataclasses
import hashlib
import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.cache import cache

import requests

logger = logging.getLogger(__name__)

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_CHARS_PER_ATTACHMENT = 4000
OCR_CACHE_TIMEOUT_SECONDS = 3600

TEXT_TYPES = {"text/plain", "text/csv", "text/markdown"}
OCR_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "image/webp",
}
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream"}

PROBLEM_UNSUPPORTED = "unsupported format"
PROBLEM_TOO_LARGE = "file too large"
PROBLEM_FAILED = "could not be read"
PROBLEM_NO_OCR = "OCR not configured"
PROBLEM_EMPTY = "no text found"
PROBLEM_LIMIT = "not read (attachment limit reached)"


@dataclasses.dataclass(frozen=True)
class AttachmentText:
    """The text of one attachment, or the reason it is missing."""

    name: str
    content_type: str
    text: str = ""
    problem: str | None = None
    sent_on: str = ""


def _content_type(attachment: dict) -> str:
    """Return the declared type, or the one guessed from the file name."""
    declared = (attachment.get("type") or "").lower()
    if declared in TEXT_TYPES or declared in OCR_TYPES:
        return declared
    guessed, _encoding = mimetypes.guess_type((attachment.get("name") or "").lower())
    if declared in GENERIC_TYPES and guessed:
        return guessed
    return declared or "application/octet-stream"


def _clip(text: str) -> str:
    """Bound the text of one attachment in the prompt."""
    text = text.strip()
    if len(text) <= MAX_CHARS_PER_ATTACHMENT:
        return text
    return f"{text[:MAX_CHARS_PER_ATTACHMENT].rstrip()}\n[truncated]"


def _decode_text(attachment: dict) -> str:
    """Decode a text attachment with its charset, UTF-8 by default."""
    charset = attachment.get("charset") or "utf-8"
    try:
        return attachment["content"].decode(charset, errors="replace")
    except LookupError:
        return attachment["content"].decode("utf-8", errors="replace")


def _ocr_text(content: bytes, content_type: str, ai_service) -> str:
    """OCR a document, cached by content so regenerating a reply is cheap."""
    digest = hashlib.sha256(content).hexdigest()
    cache_key = f"ai:attachment-ocr:{settings.AI_OCR_MODEL}:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    text = ai_service.ocr_document(content, content_type)
    cache.set(cache_key, text, OCR_CACHE_TIMEOUT_SECONDS)
    return text


def read_attachment(attachment: dict, ai_service) -> AttachmentText:
    """Extract the text of one parsed attachment, never raising on bad files."""
    name = attachment.get("name") or "unnamed attachment"
    content_type = _content_type(attachment)
    result = AttachmentText(name=name, content_type=content_type)
    content = attachment.get("content") or b""

    if content_type not in TEXT_TYPES and content_type not in OCR_TYPES:
        return dataclasses.replace(result, problem=PROBLEM_UNSUPPORTED)
    if (attachment.get("size") or len(content)) > MAX_ATTACHMENT_BYTES:
        return dataclasses.replace(result, problem=PROBLEM_TOO_LARGE)

    if content_type in TEXT_TYPES:
        text = _decode_text(attachment)
    elif not settings.AI_OCR_MODEL:
        return dataclasses.replace(result, problem=PROBLEM_NO_OCR)
    else:
        try:
            text = _ocr_text(content, content_type, ai_service)
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning("OCR failed for a %s attachment: %s", content_type, exc)
            return dataclasses.replace(result, problem=PROBLEM_FAILED)

    if not text.strip():
        return dataclasses.replace(result, problem=PROBLEM_EMPTY)
    return dataclasses.replace(result, text=_clip(text))


def _message_day(message) -> str:
    """Return the ISO date a message was sent, for the prompt."""
    moment = message.sent_at or message.created_at
    return moment.date().isoformat() if moment else ""


def read_messages_attachments(messages, ai_service) -> list[AttachmentText]:
    """Read the attachments the citizen sent in ``messages`` (chronological).

    Messages sent by the mailbox itself are skipped. The newest attachments
    are read first; beyond ``MAX_ATTACHMENTS`` the older ones are listed as
    not read, and the result is returned in chronological order.
    """
    pending = []
    for message in reversed(messages):
        if message.is_sender or not message.has_attachments:
            continue
        sent_on = _message_day(message)
        attachments = message.get_parsed_data().get("attachments") or []
        # Newest first overall, so the final reversal gives chronological order.
        pending += [(attachment, sent_on) for attachment in reversed(attachments)]

    to_read = pending[:MAX_ATTACHMENTS]
    with ThreadPoolExecutor(max_workers=MAX_ATTACHMENTS) as executor:
        read = list(
            executor.map(lambda item: read_attachment(item[0], ai_service), to_read)
        )

    results = [
        dataclasses.replace(result, sent_on=sent_on)
        for result, (_attachment, sent_on) in zip(read, to_read, strict=True)
    ]
    results += [
        AttachmentText(
            name=attachment.get("name") or "unnamed attachment",
            content_type=_content_type(attachment),
            problem=PROBLEM_LIMIT,
            sent_on=sent_on,
        )
        for attachment, sent_on in pending[MAX_ATTACHMENTS:]
    ]
    return list(reversed(results))


def format_attachments_context(attachment_texts: list[AttachmentText]) -> str:
    """Render the attachments as delimited data blocks for the prompt."""
    blocks = []
    for index, item in enumerate(attachment_texts, start=1):
        header = f"[Attachment {index}: {item.name} ({item.content_type})"
        header += f", sent on {item.sent_on}]" if item.sent_on else "]"
        body = item.text if item.problem is None else f"Not read: {item.problem}."
        blocks.append(f"{header}\n{body}\n[End of attachment {index}]")
    return "\n\n".join(blocks)

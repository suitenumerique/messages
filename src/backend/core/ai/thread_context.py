"""Build the email thread transcript sent to the AI."""

from core import models

THREAD_CONTEXT_MAX_MESSAGES = 8
THREAD_CONTEXT_MAX_CHARS_PER_MESSAGE = 2000


def clip_text(text: str, max_chars: int) -> str:
    """Keep prompt chunks bounded while preserving the beginning of each message."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n[truncated]"


def get_thread_messages(message: models.Message) -> list[models.Message]:
    """Return the latest non-draft messages of the thread, chronologically."""
    if not message.thread_id:
        return [message]

    thread_messages = list(
        models.Message.objects.select_related("sender")
        .prefetch_related("recipients__contact")
        .filter(thread_id=message.thread_id, is_draft=False)
        .order_by("-created_at", "-id")[:THREAD_CONTEXT_MAX_MESSAGES]
    )
    thread_messages.reverse()
    return thread_messages or [message]


def build_thread_context(
    message: models.Message, thread_messages: list[models.Message]
) -> str:
    """Return a compact chronological transcript for the source message thread."""
    if not message.thread_id:
        return message.get_as_text()

    entries = []
    for index, thread_message in enumerate(thread_messages, start=1):
        marker = (
            "source message" if thread_message.id == message.id else "thread message"
        )
        message_text = clip_text(
            thread_message.get_as_text(), THREAD_CONTEXT_MAX_CHARS_PER_MESSAGE
        )
        entries.append(f"[{marker} {index}]\n{message_text}")
    return "\n\n".join(entries)

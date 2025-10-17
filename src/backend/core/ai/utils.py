from typing import List

from django.conf import settings

from core.models import Message, Thread


def get_messages_from_thread(thread: Thread) -> List[Message]:
    """
    Extract messages from a thread and return them as a list of text representations using Message.get_as_text().
    """
    messages = []
    for message in thread.messages.all():
        if not (message.is_draft or message.is_trashed):
            messages.append(message)
    return messages


## Check if AI features are enabled based on settings


def is_ai_enabled() -> bool:
    """Check if AI features are enabled based on the presence of required settings in the environment (API_KEY, BASE_URL, MODEL)"""
    return all(
        [
            settings.AI_API_KEY,
            settings.AI_BASE_URL,
            settings.AI_MODEL,
        ]
    )


def is_ai_summary_enabled() -> bool:
    """
    Check if AI summary features are enabled.
    This is determined by the presence of the AI settings and if FEATURE_AI_SUMMARY is set to 1.
    """
    return all([is_ai_enabled(), settings.FEATURE_AI_SUMMARY])


def is_auto_labels_enabled() -> bool:
    """
    Check if AI auto-labeling features are enabled.
    This is determined by the presence of the AI settings and if FEATURE_AI_AUTOLABELS is set to 1.
    """
    return all([is_ai_enabled(), settings.FEATURE_AI_AUTOLABELS])

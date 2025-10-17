import json
from pathlib import Path
from typing import List

from django.conf import settings
from django.utils import translation

from core.models import Message, Thread


def get_active_language() -> str:
    """Get the active language or fallback to the default language code."""
    return translation.get_language() or settings.LANGUAGE_CODE


def load_ai_prompts() -> dict:
    """Load AI prompts from the ai_prompts.json file."""
    prompts_path = Path(__file__).parent / "ai_prompts.json"
    with open(prompts_path, encoding="utf-8") as f:
        return json.load(f)


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
            getattr(settings, "AI_API_KEY", None),
            getattr(settings, "AI_BASE_URL", None),
            getattr(settings, "AI_MODEL", None),
        ]
    )


def is_ai_summary_enabled() -> bool:
    """
    Check if AI summary feature is enabled.
    This is determined by the presence of the AI settings and if AI_FEATURE_SUMMARY_ENABLED is set to 1.
    """
    return all(
        [is_ai_enabled(), getattr(settings, "AI_FEATURE_SUMMARY_ENABLED", False)]
    )


def is_auto_labels_enabled() -> bool:
    """
    Check if AI auto-labeling features are enabled.
    This is determined by the presence of the AI settings and if AI_FEATURE_AUTO_LABELS_ENABLED is set to 1.
    """
    return all(
        [is_ai_enabled(), getattr(settings, "AI_FEATURE_AUTOLABELS_ENABLED", False)]
    )

def is_ai_messages_generation_enabled() -> bool:
    """
    Check if AI messages generation feature is enabled.
    This is determined by the presence of the AI settings and if AI_FEATURE_MESSAGES_GENERATION_ENABLED is set to 1.
    """
    return all(
        [is_ai_enabled(), getattr(settings, "AI_FEATURE_MESSAGES_GENERATION_ENABLED", False)]
    )

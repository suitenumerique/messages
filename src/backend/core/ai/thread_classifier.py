"""AI thread label classification"""

import json
from pathlib import Path

from django.conf import settings
from django.utils import timezone, translation

from core.ai.utils import get_messages_from_thread
from core.models import Thread
from core.services.ai_service import AIService


def get_most_relevant_labels(thread: Thread, labels: list) -> list[str]:
    """
    Classifies the given email(s) into the most relevant labels using an AI service.
    """
    # Prepare labels for the prompt
    labels = [
        {k: v for k, v in label.items() if k in ("name", "description")}
        for label in labels
    ]

    current_datetime = timezone.now().isoformat()

    # Determine the active or fallback language
    active_language = translation.get_language() or settings.LANGUAGE_CODE

    # Extract messages from the thread
    messages = get_messages_from_thread(thread)
    messages_as_text = "\n\n".join([message.get_as_text() for message in messages])

    # Load prompt templates from ai_prompts.json
    prompts_path = Path(__file__).parent / "ai_prompts.json"
    with open(prompts_path, encoding="utf-8") as f:
        prompts = json.load(f)

    # Get the prompt for the active language
    prompt_template = prompts.get(active_language)
    prompt_query = prompt_template["autolabels_query"]
    prompt = prompt_query.format(
        messages=messages_as_text,
        labels=labels,
        date_time=current_datetime,
        language=active_language,
    )

    with translation.override(active_language):
        best_labels = AIService().call_ai_api(prompt)

    # Get rid of surrounding text if present
    if best_labels.startswith('["') and best_labels.endswith('"]'):
        best_labels = '["' + best_labels.split('["')[1].split('"]')[0] + '"]'

    try:
        labels_list = json.loads(best_labels)
    except json.decoder.JSONDecodeError:
        labels_list = []
    return labels_list

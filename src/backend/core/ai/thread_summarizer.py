"""AI-powered thread summarization."""

import json
from pathlib import Path

from django.conf import settings

from core.ai.utils import get_messages_from_thread
from core.models import Thread
from core.services.ai_service import AIService


def summarize_thread(thread: Thread) -> str:
    """Summarizes a thread using the OpenAI client based on the configured language."""

    active_language = settings.LANGUAGE_CODE

    # Extract messages from the thread
    messages = get_messages_from_thread(thread)
    messages_as_text = "\n\n".join([message.get_as_text() for message in messages])

    # Load prompt templates from ai_prompts.json
    prompts_path = Path(__file__).parent / "ai_prompts.json"
    with open(prompts_path, encoding="utf-8") as f:
        prompts = json.load(f)

    # Get the prompt for the active language, fallback to en-us
    prompt_template = prompts.get(active_language) or prompts.get("en-us")
    if prompt_template is None:
        raise ValueError(f"No AI prompt template for language '{active_language}'")
    prompt_query = prompt_template["summary_query"]
    prompt = prompt_query.format(messages=messages_as_text, language=active_language)

    summary = AIService().call_ai_api(prompt)

    return summary

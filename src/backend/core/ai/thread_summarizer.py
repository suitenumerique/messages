from django.utils import translation

from core.ai.utils import get_active_language, get_messages_from_thread, load_ai_prompts
from core.models import Thread
from core.services.ai_service import AIService


def summarize_thread(thread: Thread) -> str:
    """Summarizes a thread using the OpenAI client based on the active Django language."""

    # Determine the active or fallback language
    active_language = get_active_language()

    # Extract messages from the thread
    messages = get_messages_from_thread(thread)
    messages_as_text = "\n\n".join([message.get_as_text() for message in messages])

    # Get the prompt for the active language
    prompts = load_ai_prompts()
    prompt_template = prompts.get(active_language)
    prompt_query = prompt_template["summary_query"]
    prompt = prompt_query.format(messages=messages_as_text, language=active_language)

    with translation.override(active_language):
        summary = AIService().call_ai_api(prompt)

    return summary

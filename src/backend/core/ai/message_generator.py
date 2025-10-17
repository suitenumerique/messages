from django.conf import settings
from django.utils import translation

from core.ai.utils import get_messages_from_thread, load_ai_prompts
from core.models import Thread
from core.services.ai_service import AIService


def generate_new_message(draft: str, user_prompt: str, name: str) -> str:
    """Generates a new mail using the AI model base on user prompt."""

    # Determine the active or fallback language
    active_language = translation.get_language() or settings.LANGUAGE_CODE

    # Get the prompt for the active language
    prompts = load_ai_prompts()
    prompt_template = prompts.get(active_language)
    prompt_query = prompt_template["new_message_generation_query"]
    prompt = prompt_query.format(
        draft=draft, language=active_language, user_prompt=user_prompt, name=name
    )

    answer = AIService().call_ai_api(prompt)

    return answer


def generate_reply_message(draft: str, thread: Thread, user_prompt: str) -> str:
    """Generates a reply message using the AI model based on the thread context and user prompt."""

    # Determine the active or fallback language
    active_language = translation.get_language() or settings.LANGUAGE_CODE

    # Extract messages from the thread
    messages = get_messages_from_thread(thread)
    messages_as_text = "\n\n".join([message.get_as_text() for message in messages])

    # Get the prompt for the active language
    prompts = load_ai_prompts()
    prompt_template = prompts.get(active_language)
    prompt_query = prompt_template["reply_message_generation_query"]
    prompt = prompt_query.format(
        draft=draft,
        messages=messages_as_text,
        language=active_language,
        user_prompt=user_prompt,
    )

    answer = AIService().call_ai_api(prompt)

    return answer

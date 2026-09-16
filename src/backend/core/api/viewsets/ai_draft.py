"""API view for generating an AI reply and saving it as a draft."""

import json
import logging
import os
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Exists, OuterRef

import rest_framework as drf
import requests
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core import enums, models
from core.mda.draft import create_draft
from core.services.ai_service import AIService

from .. import permissions, serializers

logger = logging.getLogger(__name__)

THREAD_CONTEXT_MAX_MESSAGES = 8
THREAD_CONTEXT_MAX_CHARS_PER_MESSAGE = 2000

# A line starting with "-", "*" or "•" (bullet) or "1." / "1)" (numbered).
BULLET_LINE_RE = re.compile(r"^\s*[-*•]\s+(?P<text>\S.*)$")
NUMBERED_LINE_RE = re.compile(r"^\s*\d+[.)]\s+(?P<text>\S.*)$")


def build_rag_context(results: list[dict]) -> str:
    """Build the RAG context block from the retrieved chunks.

    The excerpts are one source among others: the agent's instructions keep
    priority, so this block must not restrict the reply to the excerpts.
    """
    return "\n\n".join(
        f"[Extrait {i}]\n{result['chunk']['content']}"
        for i, result in enumerate(results, start=1)
    )


def _clip_text(text: str, max_chars: int) -> str:
    """Keep prompt chunks bounded while preserving the beginning of each message."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n[truncated]"


def _build_thread_context(message: models.Message) -> str:
    """Return a compact chronological transcript for the source message thread."""
    if not message.thread_id:
        return message.get_as_text()

    thread_messages = list(
        models.Message.objects.select_related("sender")
        .prefetch_related("recipients__contact")
        .filter(thread_id=message.thread_id, is_draft=False)
        .order_by("-created_at", "-id")[:THREAD_CONTEXT_MAX_MESSAGES]
    )
    thread_messages.reverse()

    if not thread_messages:
        return message.get_as_text()

    entries = []
    for index, thread_message in enumerate(thread_messages, start=1):
        marker = (
            "source message" if thread_message.id == message.id else "thread message"
        )
        message_text = _clip_text(
            thread_message.get_as_text(), THREAD_CONTEXT_MAX_CHARS_PER_MESSAGE
        )
        entries.append(f"[{marker} {index}]\n{message_text}")
    return "\n\n".join(entries)


def _rag_search_query(message: models.Message, current_draft_text: str | None) -> str:
    """Build the search query: email thread first, agent draft as fallback.

    The email thread is the actual question; the agent draft is only an
    intent and may be empty — it must never be sent as-is to /v1/search.
    """
    citizen_text = (_build_thread_context(message) or "").strip()
    draft_text = (current_draft_text or "").strip()
    if citizen_text and draft_text:
        return f"{citizen_text}\n\nAgent draft intent:\n{draft_text}"
    return citizen_text or draft_text

class ServiceUnavailable(drf.exceptions.APIException):
    """503 response for unavailable upstream AI service."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "service_unavailable"


def _reply_subject(subject: str | None) -> str:
    """Return a reply subject without stacking repeated Re: prefixes."""
    subject = subject or ""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}" if subject else "Re:"


def _blocknote_block(line: str) -> dict:
    """Map one line of AI text to a BlockNote paragraph or list item."""
    block_type, text = "paragraph", line
    if match := BULLET_LINE_RE.match(line):
        block_type, text = "bulletListItem", match["text"]
    elif match := NUMBERED_LINE_RE.match(line):
        block_type, text = "numberedListItem", match["text"]
    return {
        "type": block_type,
        "content": [{"type": "text", "text": text.strip(), "styles": {}}],
    }


def blocknote_blocks(text: str) -> str:
    """Serialize plain AI text into the draft editor's BlockNote JSON shape.

    Lines starting with a list marker become list items, so a list asked for
    by the agent survives in the editor.
    """
    blocks = [_blocknote_block(line) for line in text.splitlines() if line.strip()]
    return json.dumps(blocks or [{"type": "paragraph", "content": ""}])


def draft_has_list(current_draft_text: str | None) -> bool:
    """Return True when the agent's draft contains a bullet or numbered list."""
    return any(
        BULLET_LINE_RE.match(line) or NUMBERED_LINE_RE.match(line)
        for line in (current_draft_text or "").splitlines()
    )


SYSTEM_PROMPT_ROLE = (
    "You are helping a government agent draft a reply to a citizen.\n"
    "Write only the reply body. Do not include a subject line.\n\n"
    "Priority order, from highest to lowest:\n"
    "1. The agent's instructions, when provided.\n"
    "2. The official reference excerpts, when provided.\n"
    "3. The general writing rules.\n\n"
)

SYSTEM_PROMPT_AGENT_RULES = (
    "Agent's instructions:\n"
    "- The agent is the author of the reply. Their instructions are a trusted "
    "source: facts, decisions, dates and commitments they give are not "
    "inventions and must appear in the reply.\n"
    "- Treat each line or bullet point of the agent's instructions as a "
    "separate point. Address every point in the reply: never drop a point, "
    "and never merge or summarize points so much that one disappears.\n"
    "- A point is either an instruction about the reply (tone, what to "
    "mention or avoid) or content to write. Apply instructions about the "
    "reply without copying them literally; write content points into the "
    "reply.\n"
    "- When the agent gives a wording, for example in quotes, reuse it "
    "verbatim.\n"
    "- Even a very short instruction (yes/no, a day of the week) is the main "
    "intent of the reply: expand it into a complete formal reply.\n"
    "- Only exception: when a point states a fact about the citizen's case "
    "(date, amount, name, case reference) that contradicts the citizen's "
    "email, or a rule that contradicts the official excerpts, use the email "
    "or the excerpts for that fact and keep the rest of the point. Do not "
    "mention the conflict to the citizen.\n\n"
)

SYSTEM_PROMPT_WRITING_RULES = (
    "General writing rules:\n"
    "- Use a formal and professional tone, as expected from a public "
    "administration or government office.\n"
    "- Structure the reply as a professional email: a formal salutation "
    "(for example 'Madame, Monsieur'), a short body, and a formal closing "
    "formula (for example 'Je vous prie d'agréer, Madame, Monsieur, "
    "l'expression de mes salutations distinguées.') followed by the "
    "signature placeholder of the administration.\n"
    "- Be concise, ideally under 150 words. When the agent's instructions "
    "contain several points, covering all of them matters more than this "
    "length.\n"
    "- Reply only in the language of the citizen's email.\n"
    "- Do not use Markdown formatting: no headings, no bold, no italic, no "
    "asterisks.\n"
    "{list_rule}"
    "- Do not invent facts, promises, dates, or case details that come "
    "neither from the email thread, the official excerpts, nor the agent's "
    "instructions. If information is missing, ask for it briefly.\n"
    "- Consider the full email thread in chronological order. Reply to the "
    "latest/source citizen message, while preserving relevant facts, "
    "commitments, answers, and unresolved requests from earlier messages in "
    "the same thread.\n"
)

LIST_ALLOWED_RULE = (
    "- The agent's instructions contain a list: you may use a simple list in "
    "the reply; start each item on its own line with '- ' (or '1.', '2.' for "
    "a numbered list). Use a single level, no nested items, and keep the "
    "salutation, introduction and closing as sentences.\n"
)
LIST_FORBIDDEN_RULE = "- Do not use lists; write plain paragraphs.\n"


def build_system_prompt(allow_lists: bool) -> str:
    """Build the fixed rules sent as the system message."""
    list_rule = LIST_ALLOWED_RULE if allow_lists else LIST_FORBIDDEN_RULE
    return (
        SYSTEM_PROMPT_ROLE
        + SYSTEM_PROMPT_AGENT_RULES
        + SYSTEM_PROMPT_WRITING_RULES.format(list_rule=list_rule)
    )


def build_user_prompt(
    message: models.Message,
    current_draft_text: str | None = None,
    rag_context: str | None = None,
) -> str:
    """Build the content of the request: thread, excerpts, then agent's instructions.

    The agent's instructions come last, right before the reply, so they are
    the freshest context when the model starts writing.
    """
    sections = [f"Email thread:\n{_build_thread_context(message)}"]
    if rag_context:
        sections.append(f"Official reference excerpts:\n{rag_context}")
    draft_text = (current_draft_text or "").strip()
    if draft_text:
        sections.append(f"Agent's instructions (address every point):\n{draft_text}")
    sections.append("Draft reply:")
    return "\n\n".join(sections) + "\n"


def _call_ai_for_reply(
    message: models.Message,
    current_draft_text: str | None,
    rag_context: str | None = None,
) -> str:
    """Send the system rules and the user content to the AI service."""
    return AIService().call_ai_api(
        build_user_prompt(message, current_draft_text, rag_context),
        system_prompt=build_system_prompt(draft_has_list(current_draft_text)),
    )


def generate_ai_reply_body(
        message: models.Message, current_draft_text: str | None = None
) -> str:
    """Generate the reply body for a message without RAG context."""
    try:
        return _call_ai_for_reply(message, current_draft_text)
    except Exception:
        logger.exception("AI service failed without RAG context, re-raising")
        raise


def generate_ai_reply_body_with_rag(
        message: models.Message, current_draft_text: str | None = None
) -> str:
    """Generate the reply body enriched with RAG chunks from Albert API.

    If the RAG step fails or is not configured, fall back to a plain AI reply
    without context.
    """
    query = _rag_search_query(message, current_draft_text)
    rag_context = None

    if query:
        try:
            results = AIService().search_chunks(query)
            logger.info("Albert RAG: %d chunks retrieved for query: %s", len(results), query[:200])
            if results:
                rag_context = build_rag_context(results)
        except ImproperlyConfigured:
            # Pas de clé Albert configurée : réponse IA sans contexte RAG.
            logger.warning("Albert API key not configured; skipping RAG context.")
        except requests.RequestException:
            # Albert indisponible ou requête rejetée : dégradation propre.
            logger.exception("Albert RAG search failed; falling back without context.")

    return _call_ai_for_reply(message, current_draft_text, rag_context)


def generate_preview_reply_body(message: models.Message) -> str:
    """Generate a local preview reply while the AI/MCP pipeline is not wired yet."""
    sender_name = message.sender.name or message.sender.email or "there"
    return (
        f"Hello {sender_name},\n\n"
        "Thank you for your message. We have received your request and will review "
        "the information you provided.\n\n"
        "This is a preview draft generated before the AI and official document "
        "retrieval pipeline is connected. Once that pipeline is available, this "
        "draft will be replaced by an answer based on the citizen email and the "
        "retrieved official documentation.\n\n"
        "TODO TODO!!!\n\n"
        "Best regards,"
    )


@extend_schema(tags=["messages"])
class AIDraftView(APIView):
    """Generate an AI reply to a message and store the result as a draft."""

    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _get_source_message(user, message_id):
        """Return the non-draft source message if the user can read its thread."""
        try:
            return (
                models.Message.objects.select_related("sender", "thread")
                .prefetch_related("recipients__contact")
                .filter(
                    Exists(
                        models.ThreadAccess.objects.filter(
                            mailbox__accesses__user=user,
                            thread=OuterRef("thread_id"),
                        )
                    )
                )
                .get(id=message_id, is_draft=False)
            )
        except models.Message.DoesNotExist as exc:
            raise drf.exceptions.NotFound(
                "Message not found, is a draft, or access denied."
            ) from exc

    @staticmethod
    def _get_sender_mailbox(user, sender_id, thread):
        """Return the mailbox the user may use to create this reply draft."""
        if not sender_id:
            raise drf.exceptions.ValidationError({"senderId": "This field is required."})

        try:
            mailbox = models.Mailbox.objects.filter(
                id=sender_id,
                accesses__user=user,
                accesses__role__in=enums.MAILBOX_ROLES_CAN_EDIT,
            ).first()
        except (ValueError, TypeError):
            mailbox = None

        if mailbox is None:
            raise drf.exceptions.PermissionDenied(
                "You do not have permission to draft as this mailbox."
            )

        can_reply_in_thread = models.ThreadAccess.objects.filter(
            thread=thread,
            mailbox=mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        ).exists()
        if not can_reply_in_thread:
            raise drf.exceptions.PermissionDenied(
                "This mailbox cannot create a draft in the source message thread."
            )
        return mailbox

    @extend_schema(
        summary="Generate an AI reply draft",
        request=inline_serializer(
            name="AIDraftRequest",
            fields={
                "senderId": drf_serializers.UUIDField(
                    required=True,
                    help_text="Mailbox ID to use as the draft sender.",
                ),
                "currentDraftText": drf_serializers.CharField(
                    required=False,
                    allow_blank=True,
                    help_text=(
                            "Current composer draft or short intent to expand into the "
                            "AI reply."
                    ),
                ),
            },
        ),
        responses={
            201: serializers.MessageSerializer,
            400: OpenApiExample(
                "Validation Error",
                value={"senderId": "This field is required."},
            ),
            403: OpenApiExample(
                "Permission Error",
                value={"detail": "You do not have permission to draft as this mailbox."},
            ),
            404: OpenApiExample(
                "Not Found",
                value={"detail": "Message not found, is a draft, or access denied."},
            ),
            503: OpenApiExample(
                "AI Not Configured",
                value={"detail": "AI service is not configured."},
            ),
        },
        description=(
                "Generate a citizen-facing reply with the configured AI service and "
                "save it as a draft reply to the source message. The citizen email "
                "address is taken from the source message sender."
        ),
    )
    def post(self, request, message_id):
        """Generate an AI response to ``message_id`` and create a reply draft."""
        source_message = self._get_source_message(request.user, message_id)
        sender_mailbox = self._get_sender_mailbox(
            request.user,
            request.data.get("senderId"),
            source_message.thread,
        )
        current_draft_text = request.data.get("currentDraftText")

        if settings.AI_DRAFT_PREVIEW_ONLY:
            ai_reply = generate_preview_reply_body(source_message)
        else:
            try:
                ai_reply = generate_ai_reply_body_with_rag(
                    source_message, current_draft_text
                )
            except ImproperlyConfigured:
                logger.info(
                    "AI service is not configured; creating preview AI draft for message %s",
                    message_id,
                )
                ai_reply = generate_preview_reply_body(source_message)
            except Exception as exc:
                logger.exception("Failed to generate AI draft for message %s", message_id)
                raise ServiceUnavailable("Failed to generate AI draft.") from exc

        draft = create_draft(
            mailbox=sender_mailbox,
            subject=_reply_subject(source_message.subject),
            draft_body=blocknote_blocks(ai_reply),
            parent_id=str(source_message.id),
            to_emails=[source_message.sender.email],
            cc_emails=[],
            bcc_emails=[],
            attachments=[],
            user=request.user,
        )

        draft = models.Message.objects.with_read_state(sender_mailbox.id).get(
            id=draft.id
        )
        return Response(
            serializers.MessageSerializer(draft, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

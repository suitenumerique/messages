"""API view for generating an AI reply and saving it as a draft."""

import json
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Exists, OuterRef

import rest_framework as drf
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


def _blocknote_paragraphs(text: str) -> str:
    """Serialize plain AI text into the draft editor's BlockNote JSON shape."""
    paragraphs = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        paragraphs.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": paragraph, "styles": {}}],
            }
        )
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": ""}]
    return json.dumps(paragraphs)


def _build_prompt(message: models.Message, current_draft_text: str | None = None) -> str:
    """Build the prompt used to generate a citizen-facing reply."""
    draft_instruction = ""
    if current_draft_text and current_draft_text.strip():
        draft_instruction = (
            "Agent draft or intent to preserve and expand:\n"
            f"{current_draft_text.strip()}\n\n"
            "Use this draft as the main intent of the reply, even if it is very "
            "short, for example yes/no/a day of the week. Expand it into a "
            "complete formal reply suitable for a public administration or "
            "government office.\n\n"
        )

    return (
        "You are helping an agent draft a clear, polite email reply to a citizen.\n"
        "Write only the reply body. Do not include a subject line. "
        "Use a formal, professional tone suitable for a public administration "
        "or government office. "
        "Do not invent facts, promises, dates, or case details that are not in the "
        "email. If information is missing, ask for it briefly.\n\n"
        f"Citizen email:\n{message.get_as_text()}\n\n"
        f"{draft_instruction}"
        "Draft reply:\n\n"
    )


def generate_ai_reply_body(
    message: models.Message, current_draft_text: str | None = None
) -> str:
    """Generate the reply body for a message using the configured AI service."""
    return AIService().call_ai_api(_build_prompt(message, current_draft_text))


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
                ai_reply = generate_ai_reply_body(source_message, current_draft_text)
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
            draft_body=_blocknote_paragraphs(ai_reply),
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

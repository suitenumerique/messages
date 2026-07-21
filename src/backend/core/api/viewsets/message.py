"""API ViewSet for Message model."""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import HttpResponse
from django.utils import timezone

import rest_framework as drf
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.utils import ThreadStatsUpdateDeferrer

from .. import permissions, serializers

# Allowed delivery status transitions
DELIVERY_STATUS_TRANSITIONS = {
    MessageDeliveryStatusChoices.FAILED: [
        MessageDeliveryStatusChoices.CANCELLED,
        MessageDeliveryStatusChoices.RETRY,
    ],
    MessageDeliveryStatusChoices.RETRY: [
        MessageDeliveryStatusChoices.CANCELLED,
    ],
}

# Map status names to enum values
DELIVERY_STATUS_MAP = {
    "failed": MessageDeliveryStatusChoices.FAILED,
    "retry": MessageDeliveryStatusChoices.RETRY,
    "cancelled": MessageDeliveryStatusChoices.CANCELLED,
}


def validate_manual_retry(message):
    """Validate that a manual retry is allowed for this message."""
    max_age = timedelta(seconds=settings.MESSAGES_MANUAL_RETRY_MAX_AGE)
    message_age = timezone.now() - (message.sent_at or message.created_at)

    if message_age > max_age:
        return (
            False,
            f"Message sent more than {max_age} ago. Manual retry not allowed.",
        )

    return (True, None)


class MessageViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
):
    """ViewSet for Message model."""

    serializer_class = serializers.MessageSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        permissions.IsAllowedToAccess,
    ]
    pagination_class = None  # Show all messages in a thread without pagination
    queryset = models.Message.objects.all()
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        """Restrict results to messages in threads accessible by the current user."""
        user = self.request.user
        queryset = (
            super()
            .get_queryset()
            .select_related("sender_user")
            .filter(
                Exists(
                    models.ThreadAccess.objects.filter(
                        mailbox__accesses__user=user, thread=OuterRef("thread_id")
                    )
                )
            )
        )

        mailbox_id = self.request.GET.get("mailbox_id")
        if mailbox_id:
            try:
                uuid.UUID(mailbox_id)
            except ValueError as exc:
                raise drf.exceptions.ValidationError("Invalid UUID format") from exc
            queryset = queryset.with_read_state(mailbox_id)

        if self.action == "list":
            thread_id = self.request.GET.get("thread_id")
            if thread_id:
                queryset = queryset.filter(thread__id=thread_id).order_by("created_at")
            else:
                return queryset.none()

        return queryset

    def destroy(self, request, *args, **kwargs):
        """Delete a message. Object permission checked by IsAllowedToAccess."""
        # if message is the last of the thread, delete the thread
        message = self.get_object()
        thread = message.thread
        if thread.messages.count() == 1:
            # Deleting the thread will cascade delete the message
            thread.delete()
        else:
            # Deleting a visible message can leave a same-``created_at``
            # sibling as the new latest, which the ``messaged_at`` guard in
            # ``update_stats`` cannot see — force the snippet re-derivation.
            was_visible = not message.is_draft and not message.is_trashed
            message.delete()
            thread.update_stats(force_snippet=was_visible)
        return drf.response.Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="eml")
    def eml(self, request, *args, **kwargs):
        """Return the EML file for a message."""
        text_plain = request.GET.get("text_plain", "0")
        if text_plain == "1":
            content_type = "text/plain; charset=utf-8"
            headers = {}
        else:
            content_type = "message/rfc822; charset=utf-8"
            headers = {
                "Content-Disposition": 'attachment; filename="message.eml"',
            }
        message = self.get_object()
        resp = HttpResponse(
            message.blob.get_content(),
            content_type=content_type,
            headers=headers,
        )
        return resp

    @extend_schema(
        request={
            "type": "object",
            "additionalProperties": {
                "$ref": "#/components/schemas/MessageDeliveryStatusChoices"
            },
        },
        responses={
            200: inline_serializer(
                name="DeliveryStatusUpdateResponse",
                fields={"updated_count": drf_serializers.IntegerField()},
            ),
        },
        description=(
            "Update delivery status of message recipients.\n\n"
            "Request body should be a dict mapping MessageRecipient IDs to target "
            "statuses.\n"
            'Example: {"recipient_id_1": "cancelled", "recipient_id_2": "retry"}\n\n'
            "Allowed transitions:\n"
            "- FAILED -> CANCELLED\n"
            "- FAILED -> RETRY manual retry, only for messages sent within "
            "the configured max age: MESSAGES_MANUAL_RETRY_MAX_AGE\n"
            "- RETRY -> CANCELLED"
        ),
    )
    @transaction.atomic
    @action(detail=True, methods=["patch"], url_path="delivery-statuses")
    def delivery_statuses(self, request, *args, **kwargs):
        """Update delivery status of message recipients."""
        message = self.get_object()

        # Only allow updating delivery status on sent messages
        if not message.is_sender or message.is_draft or message.is_trashed:
            return drf.response.Response(
                {
                    "error": "Cannot update delivery status on received, draft or trashed messages"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate request body
        if not isinstance(request.data, dict) or not request.data:
            return drf.response.Response(
                {"error": "Request body must be a non-empty dict"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get all recipient IDs from request and validate they are valid UUIDs
        recipient_ids = list(request.data.keys())
        try:
            recipient_uuids = [uuid.UUID(rid) for rid in recipient_ids]
        except ValueError:
            return drf.response.Response(
                {"error": "Recipient IDs must be valid UUIDs"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch recipients that belong to this message
        recipients = message.recipients.filter(id__in=recipient_uuids)
        recipients_by_id = {str(r.id): r for r in recipients}

        # Validate all recipients exist and belong to this message
        missing_ids = set(recipient_ids) - set(recipients_by_id.keys())
        if missing_ids:
            return drf.response.Response(
                {"error": f"Recipients not found: {', '.join(missing_ids)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate all transitions
        errors = []
        updates = []
        for recipient_id, target_status in request.data.items():
            if target_status not in DELIVERY_STATUS_MAP:
                errors.append(f"Invalid status '{target_status}' for {recipient_id}")
                continue

            recipient = recipients_by_id[recipient_id]
            current_status = recipient.delivery_status
            target_status_value = DELIVERY_STATUS_MAP[target_status]

            if current_status not in DELIVERY_STATUS_TRANSITIONS:
                errors.append(
                    f"Cannot update from current status for recipient {recipient_id}"
                )
                continue

            if target_status_value not in DELIVERY_STATUS_TRANSITIONS[current_status]:
                current_status_name = (
                    MessageDeliveryStatusChoices(current_status).label
                    if current_status
                    else "None"
                )
                errors.append(
                    f"Transition from '{current_status_name}' to "
                    f"'{target_status}' not allowed for {recipient_id}"
                )
                continue

            # Validate age constraint for manual retry
            if target_status_value == MessageDeliveryStatusChoices.RETRY:
                is_valid, error_message = validate_manual_retry(message)
                if not is_valid:
                    errors.append(error_message)
                    continue

            updates.append((recipient, target_status_value))

        if errors:
            return drf.response.Response(
                {"error": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Apply updates (signal will update thread stats via context manager)
        with ThreadStatsUpdateDeferrer.defer():
            for recipient, new_status in updates:
                recipient.delivery_status = new_status
                update_fields = ["delivery_status"]

                # Reset retry-related fields when transitioning to RETRY
                if new_status == MessageDeliveryStatusChoices.RETRY:
                    recipient.retry_count = 0
                    recipient.retry_at = None
                    recipient.delivery_message = None
                    update_fields.extend(
                        ["retry_count", "retry_at", "delivery_message"]
                    )

                recipient.save(update_fields=update_fields)

        return drf.response.Response({"updated_count": len(updates)})

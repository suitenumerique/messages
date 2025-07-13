"""Management command to retry sending a message to failed/retry recipients."""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import models
from core.enums import MessageDeliveryStatusChoices
from core.tasks import retry_messages_task

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Management command to retry sending message(s) to recipients with retry status."""

    help = "Retry sending message(s) to recipients with retry status. Without --force, delegates to celery task (respects retry timing). With --force, processes immediately (ignores retry delays). Specify message_id for single message, or omit for bulk processing."

    def add_arguments(self, parser):
        """Define optional argument for message ID."""
        parser.add_argument(
            "message_id",
            nargs="?",
            help="ID of the message to retry sending (if not provided, retry all retryable messages)",
        )
        parser.add_argument(
            "--force-mta-out",
            action="store_true",
            help="Force sending through external MTA even for local recipients",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of messages to process in each batch (default: 100)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force immediate retry by resetting retry_at timestamps (ignores retry delays)",
        )

    def handle(self, *args, **options):
        """
        Retry sending messages to recipients with retry status.

        Without --force: Delegates to celery task (respects retry timing).
        With --force: Processes immediately (resets retry_at timestamps).
        """
        message_id = options.get("message_id")
        force_mta_out = options.get("force_mta_out", False)
        batch_size = options.get("batch_size", 100)
        force = options.get("force", False)

        if force:
            # Handle force operations: reset timestamps and delegate to celery task
            if message_id:
                # Reset timestamps for single message and delegate
                self._reset_and_delegate_single(message_id, force_mta_out)
            else:
                # Reset timestamps for all messages and delegate
                self._reset_and_delegate_all(force_mta_out, batch_size)
        else:
            # Delegate to celery task for non-force operations
            self._delegate_to_celery_task(message_id, force_mta_out, batch_size)

    def _delegate_to_celery_task(self, message_id, force_mta_out, batch_size):
        """Delegate retry operations to celery task synchronously and print result."""
        self.stdout.write("Running retry operations via celery task (synchronously)...")

        result = retry_messages_task.apply(
            args=(),
            kwargs={
                "message_id": message_id,
                "force_mta_out": force_mta_out,
                "batch_size": batch_size,
            },
        )
        if result.successful():
            self.stdout.write(
                self.style.SUCCESS(f"Task completed successfully: {result.get()}")
            )
        else:
            self.stdout.write(self.style.ERROR(f"Task failed: {result.result}"))

    def _reset_and_delegate_single(self, message_id, force_mta_out):
        """Reset retry_at timestamps for single message and delegate to celery task."""
        try:
            message = models.Message.objects.get(id=message_id)
        except models.Message.DoesNotExist:
            raise CommandError(
                f"Message with ID '{message_id}' does not exist."
            ) from None

        # Check if message is a draft
        if message.is_draft:
            raise CommandError(
                f"Message '{message_id}' is still a draft and cannot be sent."
            )

        # Get recipients with retry status
        retry_recipients = message.recipients.filter(
            delivery_status__in=[
                MessageDeliveryStatusChoices.RETRY,
                # MessageDeliveryStatusChoices.FAILED,
            ]
        )

        if not retry_recipients.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"No recipients with retry status found for message '{message_id}'"
                )
            )
            return

        # Reset retry_at timestamps
        with transaction.atomic():
            updated_count = retry_recipients.update(retry_at=None)
            self.stdout.write(
                f"Reset retry_at timestamp for {updated_count} recipient(s) of message '{message_id}'"
            )

        # Delegate to celery task
        self._delegate_to_celery_task(message_id, force_mta_out, 100)

    def _reset_and_delegate_all(self, force_mta_out, batch_size):
        """Reset retry_at timestamps for all messages and delegate to celery task."""
        # Find all messages with retryable recipients
        messages_with_retries = models.Message.objects.filter(
            is_draft=False,
            recipients__delivery_status=MessageDeliveryStatusChoices.RETRY,
        ).distinct()

        total_messages = messages_with_retries.count()

        if total_messages == 0:
            self.stdout.write(
                self.style.WARNING("No messages with retryable recipients found")
            )
            return

        self.stdout.write(
            f"Found {total_messages} message(s) with retryable recipients"
        )

        # Reset retry_at timestamps for all recipients
        with transaction.atomic():
            updated_count = models.MessageRecipient.objects.filter(
                delivery_status=MessageDeliveryStatusChoices.RETRY
            ).update(retry_at=None)

            self.stdout.write(
                f"Reset retry_at timestamp for {updated_count} recipient(s) across all messages"
            )

        # Delegate to celery task
        self._delegate_to_celery_task(None, force_mta_out, batch_size)

"""
Django management command to send emails using send_outbound_email.
This command does not write to the database and works even without any mailboxes configured.

Usage examples:
    # Send a simple email (works without any mailboxes)
    python manage.py send_mail --to recipient@example.com --subject "Test" --body "Hello World"
    
    # Send with custom sender
    python manage.py send_mail --to recipient@example.com --subject "Test" --body "Hello World" \
        --from sender@mydomain.com
    
    # Dry run to see what would be sent
    python manage.py send_mail --to recipient@example.com --subject "Test" --body "Hello World" --dry-run
"""

import base64
import logging
import uuid

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from core import models
from core.mda.outbound import send_outbound_email
from core.mda.rfc5322 import compose_email
from core.mda.signing import sign_message_dkim

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send an email using send_outbound_email (works without mailboxes, no DB writes)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            type=str,
            required=True,
            help="Recipient email address",
        )
        parser.add_argument(
            "--subject",
            type=str,
            required=True,
            help="Email subject",
        )
        parser.add_argument(
            "--body",
            type=str,
            required=True,
            help="Email body (plain text)",
        )
        parser.add_argument(
            "--from",
            type=str,
            help="Sender email address (defaults to noreply@localhost if not specified)",
            dest="from_email",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without actually sending the email",
        )

    def handle(self, *args, **options):
        to_email = options["to"]
        subject = options["subject"]
        body = options["body"]
        from_email = options.get("from_email")
        dry_run = options.get("dry_run", False)

        # Validate email addresses
        try:
            validate_email(to_email)
        except ValidationError as e:
            raise CommandError(f"Invalid recipient email address: {to_email}") from e

        if from_email:
            try:
                validate_email(from_email)
            except ValidationError as e:
                raise CommandError(f"Invalid sender email address: {from_email}") from e

        # Get sender mailbox or use minimal setup
        sender_mailbox = None
        custom_attributes = {}

        if from_email:
            try:
                sender_mailbox = models.Mailbox.objects.get(
                    local_part=from_email.split("@")[0],
                    domain__name=from_email.split("@")[1],
                )
                custom_attributes = sender_mailbox.domain.custom_attributes or {}
            except models.Mailbox.DoesNotExist:
                # Use minimal setup without mailbox
                logger.warning(
                    f"Mailbox with email '{from_email}' not found, sending without DKIM"
                )
        else:
            # Use minimal setup without mailbox
            logger.warning("No mailbox specified, sending without DKIM")
            from_email = "noreply@localhost"  # Default fallback

        from_name = (
            sender_mailbox.contact.name if sender_mailbox else None
        ) or from_email.split("@")[0]

        logger.info(f"Sending email from {from_email} to {to_email}")
        logger.info(f"Subject: {subject}")

        # Generate MIME ID
        mime_id = (
            base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode("ascii")
        )
        mime_id = f"{mime_id}@_lst.{from_email.split('@')[1]}"

        # Generate MIME content
        mime_data = {
            "from": [{"name": from_name, "email": from_email}],
            "to": [{"name": to_email.split("@")[0], "email": to_email}],
            "cc": [],
            "subject": subject,
            "textBody": [{"content": body}],
            "htmlBody": [],
            "message_id": mime_id,
        }

        # Compose the email
        raw_mime = compose_email(mime_data)

        # Sign the message with DKIM (only if mailbox exists)
        dkim_signature_header = None
        if sender_mailbox:
            dkim_signature_header = sign_message_dkim(
                raw_mime_message=raw_mime, maildomain=sender_mailbox.domain
            )

        if dkim_signature_header:
            raw_mime_signed = dkim_signature_header + b"\r\n" + raw_mime
        else:
            raw_mime_signed = raw_mime

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN MODE - Email would be sent with the following details:"
                )
            )
            self.stdout.write(f"  From: {from_name} <{from_email}>")
            self.stdout.write(f"  To: {to_email}")
            self.stdout.write(f"  Subject: {subject}")
            self.stdout.write(f"  Body: {body[:100]}{'...' if len(body) > 100 else ''}")
            self.stdout.write(f"  MIME ID: {mime_id}")
            self.stdout.write(
                f"  DKIM: {'Signed' if dkim_signature_header else 'Not signed (no mailbox/DKIM configured)'}"
            )
            return

        # Send the message using send_outbound_email
        recipient_emails = {to_email}
        statuses = send_outbound_email(
            recipient_emails, from_email, raw_mime_signed, custom_attributes
        )

        # Display results
        for recipient_email, status in statuses.items():
            if status["delivered"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Email sent successfully to {recipient_email}"
                    )
                )
            else:
                error_msg = status.get("error", "Unknown error")
                self.stdout.write(
                    self.style.ERROR(
                        f"✗ Failed to send email to {recipient_email}: {error_msg}"
                    )
                )
                if status.get("retry", False):
                    self.stdout.write(
                        f"✗ Temporary failure - would be retried. Error: {error_msg}"
                    )

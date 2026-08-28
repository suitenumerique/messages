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

import logging

from django.core.management.base import BaseCommand, CommandError

from jmap_email import ComposeError, compose_email, parse_address

from core import models
from core.mda.addresses import (
    address_domain,
    address_local_part,
    ascii_lower,
    normalize_domain,
    split_address,
)
from core.mda.outbound import send_outbound_email
from core.mda.signing import sign_message_dkim
from core.mda.utils import compose_options_for, current_sent_at, generate_mime_id

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Send an email using send_outbound_email."""

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

        # Validate email addresses through the same parser the rest of
        # the inbound / outbound pipeline uses. ``parse_address`` is
        # strict by default: ``("", "")`` on anything that isn't a real
        # addr-spec.
        _, parsed_to = parse_address(to_email)
        if not parsed_to:
            raise CommandError(f"Invalid recipient email address: {to_email}")
        to_email = parsed_to

        if from_email:
            _, parsed_from = parse_address(from_email)
            if not parsed_from:
                raise CommandError(f"Invalid sender email address: {from_email}")
            from_email = parsed_from

        # Get sender mailbox or use minimal setup
        sender_mailbox = None
        maildomain_custom_settings = {}

        # ``parse_address`` above already guaranteed a real addr-spec.
        from_parts = split_address(from_email) if from_email else None

        if from_parts:
            try:
                sender_mailbox = models.Mailbox.objects.get(
                    local_part=ascii_lower(from_parts[0]),
                    domain__name=normalize_domain(from_parts[1]),
                )
                maildomain_custom_settings = sender_mailbox.domain.custom_settings or {}
            except models.Mailbox.DoesNotExist:
                # Use minimal setup without mailbox. Log domain only —
                # the full address is PII and the local part doesn't help
                # diagnose the missing-mailbox case.
                logger.warning(
                    "Mailbox not found in domain '%s', sending without DKIM",
                    address_domain(from_email),
                )
        else:
            # Use minimal setup without mailbox
            logger.warning("No mailbox specified, sending without DKIM")
            from_email = "noreply@localhost"  # Default fallback

        from_name = (
            sender_mailbox.contact.name if sender_mailbox else None
        ) or address_local_part(from_email)

        # Domain-only in logs to avoid PII leakage; the full address is
        # in the recipient model and the MIME envelope for forensics.
        logger.info(
            "Sending email from <%s> to <%s>",
            address_domain(from_email),
            address_domain(to_email),
        )
        logger.info("Subject length: %d", len(subject or ""))

        mime_id = generate_mime_id(address_domain(from_email))

        mime_data = {
            "from": [{"name": from_name, "email": from_email}],
            "to": [{"name": address_local_part(to_email), "email": to_email}],
            "cc": [],
            "subject": subject,
            "sentAt": current_sent_at(),
            "textBody": [{"content": body}],
            "htmlBody": [],
            "messageId": [mime_id],
        }

        # Compose the email. A malformed addr-spec surfaces here rather
        # than at parse time, so it gets the same CommandError treatment.
        try:
            raw_mime = compose_email(
                mime_data, options=compose_options_for([from_email, to_email])
            )
        except ComposeError as e:
            raise CommandError(f"Cannot compose message: {e}") from e

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
            recipient_emails, from_email, raw_mime_signed, maildomain_custom_settings
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

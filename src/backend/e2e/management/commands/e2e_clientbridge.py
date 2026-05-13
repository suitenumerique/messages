"""
Django management command to bootstrap client-bridge E2E test data.

Separated from e2e_demo so that db:reset (used by non-client-bridge tests)
doesn't pay the cost of creating channels and EML blobs on every call.
"""

from email.mime.text import MIMEText
from email.utils import format_datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core import models
from core.enums import (
    CLIENT_BRIDGE_ROLE_SCOPES,
    ChannelTypes,
    MailboxRoleChoices,
    ThreadAccessRoleChoices,
)

BROWSERS = ["chromium", "firefox", "webkit"]
DOMAIN_NAME = "example.local"
SHARED_MAILBOX_LOCAL_PART = "shared.e2e"
CLIENTBRIDGE_APP_PASSWORD = "e2e-client-bridge-password"  # noqa: S105


class Command(BaseCommand):
    """Create client-bridge channels and IMAP test data for E2E testing."""

    help = "Create client-bridge E2E data (channels and IMAP test messages)"

    @transaction.atomic
    def handle(self, *args, **options):
        """Execute the command."""
        self.stdout.write(
            self.style.WARNING("\n\n|  Creating client-bridge E2E data\n")
        )

        domain = models.MailDomain.objects.get(name=DOMAIN_NAME)

        # Create channels for all regular user mailboxes
        for browser in BROWSERS:
            try:
                mailbox = models.Mailbox.objects.get(
                    local_part=f"user.e2e.{browser}", domain=domain
                )
                self._create_clientbridge_channel(mailbox)
            except models.Mailbox.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Mailbox user.e2e.{browser} not found, skipping"
                    )
                )

        # Create channel for shared mailbox
        try:
            shared_mailbox = models.Mailbox.objects.get(
                local_part=SHARED_MAILBOX_LOCAL_PART, domain=domain
            )
            self._create_clientbridge_channel(shared_mailbox)
        except models.Mailbox.DoesNotExist:
            self.stdout.write(
                self.style.WARNING("  Shared mailbox not found, skipping")
            )

        # Create IMAP test messages on the first regular user's mailbox (chromium)
        first_mailbox = models.Mailbox.objects.get(
            local_part="user.e2e.chromium", domain=domain
        )
        self._create_imap_test_messages(first_mailbox, domain)

    def _create_clientbridge_channel(self, mailbox):
        """Create a client-bridge channel with a known password for e2e testing."""
        access = models.MailboxAccess.objects.filter(
            mailbox=mailbox, role=MailboxRoleChoices.ADMIN
        ).first()
        if not access:
            self.stdout.write(
                self.style.WARNING(
                    f"  No admin user found for {mailbox}, skipping channel"
                )
            )
            return

        _channel, created = models.Channel.objects.get_or_create(
            mailbox=mailbox,
            type=ChannelTypes.CLIENT_BRIDGE,
            defaults={
                "name": f"E2E client-bridge ({mailbox})",
                "user": access.user,
                "settings": {"scopes": list(CLIENT_BRIDGE_ROLE_SCOPES["sender"])},
                "encrypted_settings": {"password": CLIENTBRIDGE_APP_PASSWORD},
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(f"  Created client-bridge channel for {mailbox}")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Client-bridge channel already exists for {mailbox}"
                )
            )

    @staticmethod
    def _make_eml(subject, sender_email, recipient_email, body, sent_at):
        """Build a minimal RFC 5322 message and return raw bytes."""
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = recipient_email
        msg["Date"] = format_datetime(sent_at)
        return msg.as_bytes()

    def _create_imap_test_messages(self, mailbox, domain):
        """Create messages for IMAP read/unread e2e testing."""
        sender_email = f"imap-sender@{domain.name}"
        recipient_email = str(mailbox)
        sender_contact, _ = models.Contact.objects.get_or_create(
            email=sender_email,
            mailbox=mailbox,
            defaults={"name": "IMAP Test Sender"},
        )

        # Thread 1: an unread message
        now = timezone.now()
        thread1 = models.Thread.objects.create(subject="IMAP unread test")
        models.ThreadAccess.objects.create(
            thread=thread1,
            mailbox=mailbox,
            role=ThreadAccessRoleChoices.EDITOR,
            read_at=None,
        )
        eml1 = self._make_eml(
            "IMAP unread test",
            sender_email,
            recipient_email,
            "This message should appear as unread in IMAP.",
            now,
        )
        blob1 = mailbox.create_blob(content=eml1, content_type="message/rfc822")
        models.Message.objects.create(
            thread=thread1,
            sender=sender_contact,
            subject="IMAP unread test",
            is_sender=False,
            is_draft=False,
            sent_at=now,
            blob=blob1,
        )
        thread1.update_stats()

        # Thread 2: a read message
        sent_at2 = now - timezone.timedelta(minutes=5)
        thread2 = models.Thread.objects.create(subject="IMAP read test")
        models.ThreadAccess.objects.create(
            thread=thread2,
            mailbox=mailbox,
            role=ThreadAccessRoleChoices.EDITOR,
            read_at=now + timezone.timedelta(minutes=1),
        )
        eml2 = self._make_eml(
            "IMAP read test",
            sender_email,
            recipient_email,
            "This message should appear as read in IMAP.",
            sent_at2,
        )
        blob2 = mailbox.create_blob(content=eml2, content_type="message/rfc822")
        models.Message.objects.create(
            thread=thread2,
            sender=sender_contact,
            subject="IMAP read test",
            is_sender=False,
            is_draft=False,
            sent_at=sent_at2,
            blob=blob2,
        )
        thread2.update_stats()

        self.stdout.write(
            self.style.SUCCESS(f"  Created IMAP test messages for {mailbox}")
        )

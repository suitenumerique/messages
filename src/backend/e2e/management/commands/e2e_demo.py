"""
Django management command to bootstrap E2E demo data.

This command creates demo users, mailboxes, shared mailboxes, and outbox test data
for E2E testing across different browsers (chromium, firefox, webkit).

Client-bridge specific data (channels, IMAP test messages) is handled by the
separate e2e_clientbridge command to avoid recreating it on every db:reset.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core import models
from core.enums import (
    CLIENT_BRIDGE_ROLE_SCOPES,
    ChannelTypes,
    MailboxRoleChoices,
    MailDomainAccessRoleChoices,
    MessageDeliveryStatusChoices,
    ThreadAccessRoleChoices,
    ThreadEventTypeChoices,
)
from core.services.identity.keycloak import get_keycloak_admin_client

BROWSERS = ["chromium", "firefox", "webkit"]
DOMAIN_NAME = "example.local"
SHARED_MAILBOX_LOCAL_PART = "shared.e2e"
IMPORT_MAILBOX_LOCAL_PART = "import.e2e"
CLIENTBRIDGE_APP_PASSWORD = "e2e-client-bridge-password"  # noqa: S105


class Command(BaseCommand):
    """Create E2E demo data for testing."""

    help = "Create E2E demo data (users, mailboxes, outbox and inbox test messages)"

    @transaction.atomic
    def handle(self, *args, **options):
        """Execute the command."""
        self.stdout.write(self.style.WARNING("\n\n|  Creating E2E Demo Data\n"))

        # Step 1: Get or create the domain
        self.stdout.write(f"\n-- 1/5 📦 Setting up domain: {DOMAIN_NAME}")
        domain, domain_created = models.MailDomain.objects.get_or_create(
            name=DOMAIN_NAME,
            defaults={
                "oidc_autojoin": True,
                "identity_sync": True,
            },
        )
        if domain_created:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Created domain: {DOMAIN_NAME}"))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ Domain already exists: {DOMAIN_NAME}")
            )

        # Step 2: Create users per browser
        self.stdout.write(
            f"\n-- 2/5 👥 Creating users for BROWSERS: {', '.join(BROWSERS)}"
        )

        regular_users = []
        mailbox_admin_users = []

        for browser in BROWSERS:
            self.stdout.write(f"\n----  Browser: {browser}")

            # Create superuser
            superuser_email = f"super_admin.e2e.{browser}@{DOMAIN_NAME}"
            self._create_user_with_mailbox(superuser_email, domain, is_superuser=True)

            # Create domain admin user and mailbox
            domain_admin_email = f"domain_admin.e2e.{browser}@{DOMAIN_NAME}"
            self._create_user_with_mailbox(
                domain_admin_email, domain, is_domain_admin=True
            )

            # Create regular user and mailbox
            regular_email = f"user.e2e.{browser}@{DOMAIN_NAME}"
            regular_user, regular_mailbox = self._create_user_with_mailbox(
                regular_email, domain
            )
            regular_users.append((regular_user, regular_mailbox))

            # Create mailbox admin user and mailbox
            mailbox_admin_email = f"mailbox_admin.e2e.{browser}@{DOMAIN_NAME}"
            mailbox_admin_user, mailbox_admin_mailbox = self._create_user_with_mailbox(
                mailbox_admin_email, domain
            )
            mailbox_admin_users.append((mailbox_admin_user, mailbox_admin_mailbox))
            self.stdout.write(
                self.style.SUCCESS(f"    ✓ Mailbox admin: {mailbox_admin_email}")
            )

        # Step 3: Create shared mailboxes
        self.stdout.write("\n-- 3/5 📥 Creating shared mailboxes")
        shared_mailbox = self._create_shared_mailbox(SHARED_MAILBOX_LOCAL_PART, domain)
        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ Shared mailbox created: {SHARED_MAILBOX_LOCAL_PART}@{DOMAIN_NAME}"
            )
        )
        import_mailbox = self._create_shared_mailbox(IMPORT_MAILBOX_LOCAL_PART, domain)
        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ Import mailbox created: {IMPORT_MAILBOX_LOCAL_PART}@{DOMAIN_NAME}"
            )
        )

        # Step 4: Add all regular users with sender role to the shared mailbox
        self.stdout.write(
            "\n-- 4/5 🔐 Adding users to shared mailboxes with appropriate roles"
        )
        for user, _ in regular_users:
            self._add_mailbox_access(shared_mailbox, user, MailboxRoleChoices.SENDER)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ Added {user.email} as SENDER to shared mailbox"
                )
            )
            self._add_mailbox_access(import_mailbox, user, MailboxRoleChoices.ADMIN)
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ Added {user.email} as ADMIN to shared mailbox")
            )

        # Step 5: Add mailbox admin users with admin role to the shared mailbox
        for user, _ in mailbox_admin_users:
            self._add_mailbox_access(shared_mailbox, user, MailboxRoleChoices.ADMIN)
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ Added {user.email} as ADMIN to shared mailbox")
            )
            self._add_mailbox_access(import_mailbox, user, MailboxRoleChoices.ADMIN)
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ Added {user.email} as ADMIN to import mailbox")
            )

        # Step 6: Create outbox test data for each browser
        self.stdout.write("\n-- 5/7 📬 Creating outbox test data")
        for browser in BROWSERS:
            self._create_outbox_test_data(domain, browser)

        # Step 7: Create inbox test data for each browser
        self.stdout.write("\n-- 6/8 📥 Creating inbox test data")
        for browser in BROWSERS:
            self._create_inbox_test_data(domain, browser)

        # Step 8: Create shared mailbox thread data for IM testing
        self.stdout.write("\n-- 7/8 💬 Creating shared mailbox thread for IM testing")
        self._create_shared_mailbox_thread_data(shared_mailbox, regular_users)

        # Step 9: Create client-bridge channels and IMAP test data
        if settings.FEATURE_CLIENTBRIDGE:
            self.stdout.write(
                "\n-- 8/8 📧 Creating client-bridge channels and IMAP test data"
            )
            for _user, mailbox in regular_users:
                self._create_clientbridge_channel(mailbox)
            self._create_clientbridge_channel(shared_mailbox)
            # Create IMAP test messages on the first regular user's mailbox
            _first_user, first_mailbox = regular_users[0]
            self._create_imap_test_messages(first_mailbox, domain)

    def _create_user_with_mailbox(
        self, email, domain, is_domain_admin=False, is_superuser=False
    ):
        """Create a user with a personal mailbox."""
        local_part = email.split("@")[0]
        full_name = local_part.replace(".", " ").replace("-", " ").title()

        # Create or get user
        user, _created = models.User.objects.get_or_create(
            email=email,
            defaults={
                "is_superuser": is_superuser,
                "full_name": full_name,
                "password": "!",
            },
        )

        keycloak_admin = get_keycloak_admin_client()
        user_id = None

        # Create or get mailbox
        mailbox, _created = models.Mailbox.objects.get_or_create(
            local_part=local_part,
            domain=domain,
            defaults={
                "is_identity": True,
            },
        )

        # Create or get contact
        contact, _ = models.Contact.objects.get_or_create(
            email=email,
            mailbox=mailbox,
            defaults={"name": full_name},
        )
        if not mailbox.contact:
            mailbox.contact = contact
            mailbox.save()

        # Give the user admin access to their own mailbox
        models.MailboxAccess.objects.get_or_create(
            mailbox=mailbox,
            user=user,
            defaults={"role": MailboxRoleChoices.ADMIN},
        )

        # If this is a domain admin, grant domain access
        if is_domain_admin:
            models.MailDomainAccess.objects.get_or_create(
                maildomain=domain,
                user=user,
                defaults={"role": MailDomainAccessRoleChoices.ADMIN},
            )

        # Set password for user in OIDC
        users = get_keycloak_admin_client().get_users({"email": str(mailbox)})
        if len(users) > 0:
            user_id = users[0].get("id")
            keycloak_admin.set_user_password(
                user_id=user_id,
                password="e2e",  # noqa: S106
                temporary=False,
            )
            self.stdout.write(
                self.style.SUCCESS(f"✓ Password set for user {user.email} in Keycloak.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"✗ User {user.email} not found in Keycloak.")
            )

        return user, mailbox

    def _create_shared_mailbox(self, local_part, domain):
        """Create a shared mailbox."""
        email = f"{local_part}@{domain.name}"
        mailbox_name = local_part.replace("-", " ").title()

        # Create or get mailbox
        mailbox, _created = models.Mailbox.objects.get_or_create(
            local_part=local_part,
            domain=domain,
            defaults={
                "is_identity": False,  # Shared mailbox
            },
        )

        # Create or get contact for the shared mailbox
        contact, _ = models.Contact.objects.get_or_create(
            email=email,
            mailbox=mailbox,
            defaults={"name": mailbox_name},
        )
        if not mailbox.contact:
            mailbox.contact = contact
            mailbox.save()

        return mailbox

    def _add_mailbox_access(self, mailbox, user, role):
        """Add or update mailbox access for a user."""
        access, created = models.MailboxAccess.objects.get_or_create(
            mailbox=mailbox,
            user=user,
            defaults={"role": role},
        )
        if not created and access.role != role:
            access.role = role
            access.save()
        return access

    def _create_outbox_test_data(self, domain, browser):
        """Create outbox test data for a specific browser."""
        self.stdout.write(f"\n----  Browser: {browser}")

        # Get the user mailbox
        try:
            mailbox = models.Mailbox.objects.get(
                local_part=f"user.e2e.{browser}", domain=domain
            )
        except models.Mailbox.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"  ✗ Mailbox not found: user.e2e.{browser}")
            )
            return

        # Create the sender contact
        sender_contact, _ = models.Contact.objects.get_or_create(
            email=str(mailbox),
            mailbox=mailbox,
            defaults={"name": f"User E2E {browser}"},
        )

        # Clean up existing outbox test threads for this mailbox
        outbox_subjects = [
            "Test message with delivery failure",
            "Test message with pending delivery",
        ]
        existing_threads = models.Thread.objects.filter(
            subject__in=outbox_subjects,
            accesses__mailbox=mailbox,
        )
        deleted_count = existing_threads.count()
        if deleted_count > 0:
            existing_threads.delete()
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠ Deleted {deleted_count} existing outbox test thread(s)"
                )
            )

        # Thread 1: Message with delivery failure (FAILED + RETRY + SENT)
        self.stdout.write("  Creating thread with delivery failure...")
        self._create_thread_with_message(
            mailbox=mailbox,
            sender_contact=sender_contact,
            subject="Test message with delivery failure",
            recipients=[
                (
                    "failed@external.invalid",
                    "Failed Recipient",
                    MessageDeliveryStatusChoices.FAILED,
                    "Recipient address rejected: Domain not found",
                    {},
                ),
                (
                    "retry@external.invalid",
                    "Retry Recipient",
                    MessageDeliveryStatusChoices.RETRY,
                    "Temporary failure, will retry",
                    {"retry_at": timezone.now() + timezone.timedelta(hours=1)},
                ),
                (
                    "sent@external.invalid",
                    "Sent Recipient",
                    MessageDeliveryStatusChoices.SENT,
                    None,
                    {"delivered_at": timezone.now()},
                ),
            ],
        )

        # Thread 2: Message with pending delivery only (RETRY + SENT, no FAILED)
        self.stdout.write("  Creating thread with pending delivery...")
        self._create_thread_with_message(
            mailbox=mailbox,
            sender_contact=sender_contact,
            subject="Test message with pending delivery",
            recipients=[
                (
                    "pending1@external.invalid",
                    "Pending Recipient 1",
                    MessageDeliveryStatusChoices.RETRY,
                    "Temporary failure, will retry",
                    {"retry_at": timezone.now() + timezone.timedelta(hours=1)},
                ),
                (
                    "pending2@external.invalid",
                    "Pending Recipient 2",
                    MessageDeliveryStatusChoices.RETRY,
                    "Server temporarily unavailable",
                    {"retry_at": timezone.now() + timezone.timedelta(hours=2)},
                ),
                (
                    "delivered@external.invalid",
                    "Delivered Recipient",
                    MessageDeliveryStatusChoices.SENT,
                    None,
                    {"delivered_at": timezone.now()},
                ),
            ],
        )

        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Outbox test data created for {browser}")
        )

    def _create_inbox_test_data(self, domain, browser):
        """Create inbox test data (received threads) for a specific browser."""
        self.stdout.write(f"\n----  Browser: {browser}")

        try:
            mailbox = models.Mailbox.objects.get(
                local_part=f"user.e2e.{browser}", domain=domain
            )
        except models.Mailbox.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"  ✗ Mailbox not found: user.e2e.{browser}")
            )
            return

        inbox_subjects = [
            "Inbox thread alpha",
            "Inbox thread beta",
        ]

        # Clean up existing inbox test threads
        existing = models.Thread.objects.filter(
            subject__in=inbox_subjects,
            accesses__mailbox=mailbox,
        )
        deleted_count = existing.count()
        if deleted_count > 0:
            existing.delete()
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠ Deleted {deleted_count} existing inbox test thread(s)"
                )
            )

        for subject in inbox_subjects:
            sender_contact, _ = models.Contact.objects.get_or_create(
                email=f"external.{browser}@external.invalid",
                mailbox=mailbox,
                defaults={"name": f"External Sender {browser}"},
            )

            thread = models.Thread.objects.create(subject=subject)
            models.ThreadAccess.objects.create(
                thread=thread,
                mailbox=mailbox,
                role=ThreadAccessRoleChoices.VIEWER,
            )
            models.Message.objects.create(
                thread=thread,
                sender=sender_contact,
                subject=subject,
                is_sender=False,
                is_draft=False,
            )
            thread.update_stats()

        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Inbox test data created for {browser}")
        )

    def _create_thread_with_message(self, mailbox, sender_contact, subject, recipients):
        """
        Create a thread with a message and recipients.

        Args:
            mailbox: The mailbox that owns the thread
            sender_contact: The sender contact
            subject: The thread/message subject
            recipients: List of tuples (email, name, status, message, extra_fields)

        Returns:
            The created thread
        """
        thread = models.Thread.objects.create(subject=subject)

        models.ThreadAccess.objects.create(
            thread=thread,
            mailbox=mailbox,
            role=ThreadAccessRoleChoices.EDITOR,
        )

        message = models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=subject,
            is_sender=True,
            is_draft=False,
            sent_at=timezone.now(),
        )

        for email, name, status, delivery_message, extra_fields in recipients:
            contact, _ = models.Contact.objects.get_or_create(
                email=email,
                mailbox=mailbox,
                defaults={"name": name},
            )
            recipient_data = {
                "message": message,
                "contact": contact,
                "delivery_status": status,
                "delivery_message": delivery_message,
                **extra_fields,
            }
            models.MessageRecipient.objects.create(**recipient_data)

        thread.update_stats()

        return thread

    def _create_shared_mailbox_thread_data(self, shared_mailbox, regular_users):
        """Create a thread in the shared mailbox for testing internal messages (IM).

        Also seeds one pre-aged ThreadEvent per browser user so the
        "edit delay elapsed" e2e test can assert on a message whose
        `is_editable` has already flipped to false — no runtime ageing
        required, keeps the test deterministic and fast.
        """
        subject = "Shared inbox thread for IM"

        # Clean up existing thread
        existing = models.Thread.objects.filter(
            subject=subject,
            accesses__mailbox=shared_mailbox,
        )
        deleted_count = existing.count()
        if deleted_count > 0:
            existing.delete()
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠ Deleted {deleted_count} existing shared mailbox IM thread(s)"
                )
            )

        # Create thread with EDITOR access so the IM input is visible
        thread = models.Thread.objects.create(subject=subject)
        models.ThreadAccess.objects.create(
            thread=thread,
            mailbox=shared_mailbox,
            role=ThreadAccessRoleChoices.EDITOR,
        )

        # Create an external sender message so the thread appears in inbox
        sender_contact, _ = models.Contact.objects.get_or_create(
            email="external@external.invalid",
            mailbox=shared_mailbox,
            defaults={"name": "External Sender"},
        )
        models.Message.objects.create(
            thread=thread,
            sender=sender_contact,
            subject=subject,
            is_sender=False,
            is_draft=False,
        )

        # Seed one pre-aged IM ThreadEvent per browser user.
        #
        # The "edit delay elapsed" test needs a ThreadEvent whose
        # `created_at` is older than `MAX_THREAD_EVENT_EDIT_DELAY`, so that
        # `is_editable` returns false and the UI hides Edit/Delete actions.
        #
        # Each browser gets its own aged event authored by its own user so
        # the `canModify = isAuthor && is_editable` check in the frontend
        # exercises the `is_editable: false` branch (not the `isAuthor: false`
        # one).
        past = timezone.now() - timezone.timedelta(
            seconds=settings.MAX_THREAD_EVENT_EDIT_DELAY + 1
        )
        for user, _mailbox in regular_users:
            # Extract "{browser}" from "user.e2e.{browser}@{domain}".
            browser = user.email.split("@")[0].removeprefix("user.e2e.")
            content = f"[e2e-aged-{browser}] Message past edit delay"
            event = models.ThreadEvent.objects.create(
                thread=thread,
                type=ThreadEventTypeChoices.IM,
                author=user,
                data={"content": content},
            )
            # Bypass `auto_now` / `auto_now_add` with a raw UPDATE so the
            # timestamps actually land in the past.
            models.ThreadEvent.objects.filter(pk=event.pk).update(
                created_at=past, updated_at=past
            )
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ Seeded aged ThreadEvent for {user.email}")
            )

        thread.update_stats()

        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Shared mailbox IM thread created: {subject}")
        )

    def _create_clientbridge_channel(self, mailbox):
        """Create a client-bridge channel with a known password for e2e testing."""
        # Use the first user with admin access to this mailbox
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
        """Create messages for IMAP read/unread e2e testing.

        Creates two threads with proper EML blobs so the client-bridge
        can serve envelope data (subject, from, date) over IMAP.
        """
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
            read_at=None,  # No read_at → all messages unread
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
            read_at=now
            + timezone.timedelta(minutes=1),  # read_at after message created_at → read
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

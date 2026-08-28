"""Management command to print user emails."""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef

from core import models
from core.mda.addresses import ascii_lower, split_address

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    """Print a list of user emails, ordered by domain name then prefix."""

    help = (
        "Print a list of user emails (from openid), ordered by domain name then prefix"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-messages",
            action="store_true",
            help="Only show users who have access to any mailbox with non-trashed messages",
        )
        parser.add_argument(
            "--admins",
            action="store_true",
            help="Only show users who are admins of at least one mail domain",
        )
        parser.add_argument(
            "--add-maildomain-custom-attribute",
            type=str,
            default=None,
            help=(
                "Add a mail domain custom attribute to the output. "
                "Prints lines as '[attribute_value],[email]'. "
                "Users are listed once per mail domain they belong to."
            ),
        )

    def handle(self, *args, **options):
        with_messages = options.get("with_messages", False)
        admins_only = options.get("admins", False)
        custom_attr = options.get("add_maildomain_custom_attribute")

        # Base queryset: all users with emails
        users = User.objects.filter(email__isnull=False).exclude(email="")

        # If --admins flag is set, filter to users who are admins of any mail domain
        if admins_only:
            users = users.filter(
                maildomain_accesses__isnull=False,
            ).distinct()

        # If --with-messages flag is set, filter to users who have access to
        # at least one mailbox with non-trashed messages
        elif with_messages:
            # Subquery to check if a mailbox has any non-trashed messages
            # User -> MailboxAccess -> Mailbox -> ThreadAccess -> Thread -> Message
            mailbox_with_messages = models.Mailbox.objects.filter(
                accesses__user=OuterRef("pk"),
                thread_accesses__thread__messages__is_trashed=False,
            )

            users = users.annotate(
                has_mailbox_with_messages=Exists(mailbox_with_messages)
            ).filter(has_mailbox_with_messages=True)

        # Sort helper by domain then prefix (local part)
        def sort_key(email):
            parts = split_address(email)
            if parts is None:
                return ("", email)
            local_part, domain = parts
            return (ascii_lower(domain), ascii_lower(local_part))

        if custom_attr:
            # Build (attribute_value, email) pairs from each user's mail domains
            rows = []
            for user in users.prefetch_related("maildomain_accesses__maildomain"):
                for access in user.maildomain_accesses.all():
                    attr_value = access.maildomain.custom_attributes.get(
                        custom_attr, ""
                    )
                    rows.append((attr_value, user.email))

            rows.sort(key=lambda row: sort_key(row[1]))

            if not rows:
                self.stdout.write(self.style.WARNING("No users found."))
                return

            self.stdout.write(self.style.SUCCESS(f"Found {len(rows)} entry(ies):\n"))
            for attr_value, email in rows:
                self.stdout.write(f"{attr_value},{email}")
        else:
            user_list = list(users.values_list("email", flat=True))
            user_list.sort(key=sort_key)

            if not user_list:
                self.stdout.write(self.style.WARNING("No users found."))
                return

            self.stdout.write(self.style.SUCCESS(f"Found {len(user_list)} user(s):\n"))
            for email in user_list:
                self.stdout.write(email)

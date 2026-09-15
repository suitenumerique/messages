"""Seed one citizen email into a mailbox for local draft/reply testing."""

from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from django.core.management.base import BaseCommand, CommandError

from jmap_email import parse_email

from core import models
from core.mda.inbound import deliver_inbound_message

import json


class Command(BaseCommand):
    """Create a realistic inbound citizen email in an existing mailbox."""

    help = "Seed one citizen email into a mailbox for testing replies and drafts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mailbox",
            required=True,
            help="Target mailbox email address, for example user1@example.local.",
        )
        parser.add_argument(
            "--data-file",
            required=True,
            help="Path to a JSON file containing the email data.",
        )

    def handle(self, *args, **options):
        mailbox_email = options["mailbox"]
        mailbox = self._get_mailbox(mailbox_email)

        # Load email data from the specified JSON file
        with open(options["data_file"], "r") as f:
            data = json.load(f)

            for infos in data:
                raw_message = self._build_raw_message(
                    to_email=mailbox_email,
                    from_email=infos["email"],
                    from_name=infos["name"],
                    subject=infos["subject"],
                    body=infos["message"],
                )

                delivered = deliver_inbound_message(
                    mailbox_email,
                    parse_email(raw_message),
                    raw_message,
                    is_import=True,
                )
                if not delivered:
                    raise CommandError(f"Could not deliver seed email to {mailbox_email}.")

                thread = (
                    models.Thread.objects.filter(
                        subject=infos["subject"],
                        accesses__mailbox=mailbox,
                    )
                    .order_by("-created_at")
                    .first()
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Seeded citizen email in {mailbox_email}"
                        + (f" (thread {thread.id})" if thread else "")
                    )
                )

    @staticmethod
    def _get_mailbox(email_address):
        try:
            local_part, domain_name = email_address.rsplit("@", 1)
        except ValueError as exc:
            raise CommandError("--mailbox must be a full email address.") from exc

        mailbox = models.Mailbox.objects.filter(
            local_part=local_part,
            domain__name=domain_name,
        ).first()
        if mailbox is None:
            raise CommandError(f"Mailbox does not exist: {email_address}")
        return mailbox

    @staticmethod
    def _build_raw_message(to_email, from_email, from_name, subject, body):
        message = EmailMessage()
        message["From"] = f"{from_name} <{from_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="seed.messages.local")
        message.set_content(body)
        return message.as_bytes()

"""Seed citizen email conversations into a mailbox for local draft/reply testing."""

import json
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from django.core.management.base import BaseCommand, CommandError

from jmap_email import parse_email

from core import models
from core.mda.inbound import deliver_inbound_message


class Command(BaseCommand):
    """Create realistic citizen email conversations in an existing mailbox."""

    help = "Seed citizen email conversations into a mailbox for testing replies."

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
                for seed_message in self._iter_seed_messages(infos, mailbox_email):
                    raw_message = self._build_raw_message(**seed_message["raw"])

                    delivered = deliver_inbound_message(
                        mailbox_email,
                        parse_email(raw_message),
                        raw_message,
                        is_import=True,
                        is_import_sender=seed_message["is_import_sender"],
                    )
                    if not delivered:
                        raise CommandError(
                            f"Could not deliver seed email to {mailbox_email}."
                        )

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
    def _iter_seed_messages(infos, mailbox_email):
        """Yield raw message parameters for one JSON entry.

        Legacy entries create one inbound citizen message. Entries with a
        ``messages`` array create a real conversation; ``direction`` can be
        ``inbound`` or ``outbound``.
        """
        if "messages" not in infos:
            yield {
                "is_import_sender": False,
                "raw": {
                    "to_email": mailbox_email,
                    "from_email": infos["email"],
                    "from_name": infos["name"],
                    "subject": infos["subject"],
                    "body": infos["message"],
                },
            }
            return

        references = []
        previous_message_id = None
        for index, item in enumerate(infos["messages"], start=1):
            direction = item.get("direction", "inbound")
            if direction not in {"inbound", "outbound"}:
                raise CommandError(
                    f"Unsupported seed message direction: {direction!r}."
                )

            message_id = item.get("message_id") or make_msgid(
                idstring=f"seed-{index}", domain="seed.messages.local"
            )
            subject = item.get("subject") or infos["subject"]

            if direction == "outbound":
                from_email = mailbox_email
                from_name = item.get("from_name") or mailbox_email.split("@", 1)[0]
                to_email = infos["email"]
                is_import_sender = True
            else:
                from_email = infos["email"]
                from_name = infos["name"]
                to_email = mailbox_email
                is_import_sender = False

            yield {
                "is_import_sender": is_import_sender,
                "raw": {
                    "to_email": to_email,
                    "from_email": from_email,
                    "from_name": from_name,
                    "subject": subject,
                    "body": item["message"],
                    "date": item.get("date"),
                    "message_id": message_id,
                    "in_reply_to": item.get("in_reply_to") or previous_message_id,
                    "references": item.get("references") or references,
                },
            }

            previous_message_id = message_id
            references = [*references, message_id]

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
    def _build_raw_message(
        to_email,
        from_email,
        from_name,
        subject,
        body,
        date=None,
        message_id=None,
        in_reply_to=None,
        references=None,
    ):
        message = EmailMessage()
        message["From"] = f"{from_name} <{from_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message["Date"] = date or formatdate(localtime=True)
        message["Message-ID"] = message_id or make_msgid(domain="seed.messages.local")
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = " ".join(references)
        message.set_content(body)
        return message.as_bytes()

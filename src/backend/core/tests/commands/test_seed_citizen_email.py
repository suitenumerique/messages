"""Tests for seeding citizen emails with attachments."""

import json
from email import message_from_bytes

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from core import factories, models
from core.management.commands.seed_citizen_email import Command


def test_build_raw_message_attaches_files_with_their_type(tmp_path):
    """Attachments are added as MIME parts with a guessed content type."""
    (tmp_path / "facture.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "recu.txt").write_text("Montant : 2,40 €", encoding="utf-8")

    raw = Command._build_raw_message(  # pylint: disable=protected-access
        to_email="agent@example.local",
        from_email="lea@citoyen.example",
        from_name="Léa Fontaine",
        subject="Dossier",
        body="Bonjour",
        attachments=[
            {"path": tmp_path / "facture.pdf"},
            {"path": tmp_path / "recu.txt", "filename": "reçu.txt"},
        ],
    )

    parts = [
        (part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
        for part in message_from_bytes(raw).walk()
        if part.get_filename()
    ]
    assert parts == [
        ("facture.pdf", "application/pdf", b"%PDF-1.4"),
        ("reçu.txt", "text/plain", "Montant : 2,40 €".encode()),
    ]


def test_build_raw_message_rejects_missing_attachment(tmp_path):
    """A wrong path in the seed file fails loudly."""
    with pytest.raises(CommandError, match="absent.pdf"):
        Command._build_raw_message(  # pylint: disable=protected-access
            to_email="agent@example.local",
            from_email="lea@citoyen.example",
            from_name="Léa Fontaine",
            subject="Dossier",
            body="Bonjour",
            attachments=[{"path": tmp_path / "absent.pdf"}],
        )


@pytest.mark.django_db
def test_seed_delivers_message_with_attachments(tmp_path):
    """Seeded messages keep their attachments, readable by the AI draft."""
    mailbox = factories.MailboxFactory()
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "facture.pdf").write_bytes(b"%PDF-1.4")
    data_file = tmp_path / "emails.json"
    data_file.write_text(
        json.dumps(
            [
                {
                    "email": "lea@citoyen.example",
                    "name": "Léa Fontaine",
                    "subject": "Dossier passeport",
                    "message": "Bonjour, voici ma facture.",
                    "attachments": [{"path": "attachments/facture.pdf"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    call_command("seed_citizen_email", mailbox=str(mailbox), data_file=str(data_file))

    message = models.Message.objects.get(subject="Dossier passeport")
    assert message.has_attachments
    attachments = message.get_parsed_data()["attachments"]
    assert [(item["name"], item["content"]) for item in attachments] == [
        ("facture.pdf", b"%PDF-1.4")
    ]

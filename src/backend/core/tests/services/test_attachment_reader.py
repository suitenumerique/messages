"""Tests for reading the citizen's attachments before drafting an AI reply."""

import datetime
import threading
import uuid
from types import SimpleNamespace

import pytest
import requests

from core.ai import attachment_reader
from core.ai.attachment_reader import (
    AttachmentText,
    format_attachments_context,
    read_attachment,
    read_messages_attachments,
)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FakeOCRService:
    """Record OCR calls and return a canned text."""

    def __init__(self, text="texte extrait", error=None):
        self.text = text
        self.error = error
        self.calls = []
        self._lock = threading.Lock()

    def ocr_document(self, content, content_type):
        """Return the canned text or raise the configured error."""
        with self._lock:
            self.calls.append((content, content_type))
        if self.error:
            raise self.error
        return self.text


def make_attachment(name, content_type, content=None, size=None):
    """Build a parsed JMAP attachment dict, unique per call to avoid cache hits."""
    content = content if content is not None else uuid.uuid4().bytes
    return {
        "name": name,
        "type": content_type,
        "content": content,
        "size": size if size is not None else len(content),
    }


def make_message(attachments, is_sender=False, day=14):
    """Build a message-like object exposing parsed attachments."""
    return SimpleNamespace(
        is_sender=is_sender,
        has_attachments=bool(attachments),
        sent_at=datetime.datetime(2026, 9, day, 18, 45, tzinfo=datetime.UTC),
        created_at=datetime.datetime(2026, 9, day, 18, 45, tzinfo=datetime.UTC),
        get_parsed_data=lambda: {"attachments": attachments},
    )


@pytest.fixture(autouse=True)
def fixture_ocr_model(settings):
    """OCR is configured unless a test says otherwise."""
    settings.AI_OCR_MODEL = "ocr-model"


def test_text_attachment_is_decoded_without_ocr():
    """Plain text files are read directly."""
    ocr = FakeOCRService()
    attachment = make_attachment("recu.txt", "text/plain", "Montant : 2,40 €".encode())

    result = read_attachment(attachment, ocr)

    assert result == AttachmentText(
        name="recu.txt", content_type="text/plain", text="Montant : 2,40 €"
    )
    assert not ocr.calls


@pytest.mark.parametrize(
    "content_type", ["application/pdf", "image/png", "image/jpeg", DOCX_TYPE]
)
def test_documents_and_images_go_through_ocr(content_type):
    """PDF, images and Word documents are sent to the OCR."""
    ocr = FakeOCRService(text="Date d'émission : 14 mars 2025")
    attachment = make_attachment("piece", content_type)

    result = read_attachment(attachment, ocr)

    assert result.text == "Date d'émission : 14 mars 2025"
    assert result.problem is None
    assert ocr.calls == [(attachment["content"], content_type)]


def test_generic_content_type_is_guessed_from_the_file_name():
    """Mail clients often send application/octet-stream for PDF files."""
    ocr = FakeOCRService()
    attachment = make_attachment("facture.PDF", "application/octet-stream")

    result = read_attachment(attachment, ocr)

    assert result.content_type == "application/pdf"
    assert ocr.calls == [(attachment["content"], "application/pdf")]


def test_ocr_result_is_cached_by_content():
    """Generating the reply twice does not OCR the same file twice."""
    ocr = FakeOCRService()
    content = uuid.uuid4().bytes

    read_attachment(make_attachment("a.pdf", "application/pdf", content), ocr)
    read_attachment(make_attachment("copie.pdf", "application/pdf", content), ocr)

    assert len(ocr.calls) == 1


def test_unsupported_format_is_reported_not_read():
    """Unknown formats are listed with the reason they were not read."""
    ocr = FakeOCRService()

    result = read_attachment(make_attachment("archive.zip", "application/zip"), ocr)

    assert result.text == ""
    assert result.problem == "unsupported format"
    assert not ocr.calls


def test_too_large_attachment_is_not_sent_to_ocr():
    """Huge files are skipped before any API call."""
    ocr = FakeOCRService()
    attachment = make_attachment(
        "scan.pdf",
        "application/pdf",
        size=attachment_reader.MAX_ATTACHMENT_BYTES + 1,
    )

    result = read_attachment(attachment, ocr)

    assert result.problem == "file too large"
    assert not ocr.calls


def test_ocr_failure_is_reported_without_raising():
    """An OCR error on one file does not break the reply generation."""
    ocr = FakeOCRService(error=requests.HTTPError("Albert API 503"))

    result = read_attachment(make_attachment("scan.png", "image/png"), ocr)

    assert result.problem == "could not be read"


def test_ocr_not_configured_still_reads_text_files(settings):
    """Without an OCR model, only text files are read."""
    settings.AI_OCR_MODEL = None
    ocr = FakeOCRService()

    pdf = read_attachment(make_attachment("a.pdf", "application/pdf"), ocr)
    txt = read_attachment(make_attachment("a.txt", "text/plain", b"ok"), ocr)

    assert pdf.problem == "OCR not configured"
    assert txt.text == "ok"
    assert not ocr.calls


def test_empty_ocr_text_is_reported():
    """A blank page is not presented as a read document."""
    result = read_attachment(
        make_attachment("vide.pdf", "application/pdf"), FakeOCRService(text="  \n")
    )

    assert result.problem == "no text found"


def test_long_text_is_truncated():
    """Each attachment is bounded in the prompt."""
    text = "a" * (attachment_reader.MAX_CHARS_PER_ATTACHMENT + 50)

    result = read_attachment(
        make_attachment("long.txt", "text/plain", text.encode()), FakeOCRService()
    )

    assert len(result.text) < len(text)
    assert result.text.endswith("[truncated]")


def test_only_citizen_attachments_are_read():
    """Attachments sent by the agent's mailbox are ignored."""
    ocr = FakeOCRService()
    citizen = make_message([make_attachment("facture.pdf", "application/pdf")])
    agent = make_message(
        [make_attachment("formulaire.pdf", "application/pdf")], is_sender=True
    )

    results = read_messages_attachments([citizen, agent], ocr)

    assert [result.name for result in results] == ["facture.pdf"]


def test_attachment_limit_keeps_newest_and_reports_the_rest(monkeypatch):
    """Beyond the limit, older attachments are listed as not read."""
    monkeypatch.setattr(attachment_reader, "MAX_ATTACHMENTS", 2)
    ocr = FakeOCRService()
    old = make_message([make_attachment("ancien.pdf", "application/pdf")], day=1)
    new = make_message(
        [
            make_attachment("recent1.pdf", "application/pdf"),
            make_attachment("recent2.pdf", "application/pdf"),
        ],
        day=14,
    )

    results = read_messages_attachments([old, new], ocr)

    by_name = {result.name: result for result in results}
    assert by_name["recent1.pdf"].problem is None
    assert by_name["recent2.pdf"].problem is None
    assert by_name["ancien.pdf"].problem == "not read (attachment limit reached)"
    assert len(ocr.calls) == 2


def test_format_attachments_context_marks_data_boundaries():
    """Each attachment is delimited and unread ones explain why."""
    context = format_attachments_context(
        [
            AttachmentText(
                name="facture.pdf",
                content_type="application/pdf",
                text="Date d'émission : 14 mars 2025",
                sent_on="2026-09-14",
            ),
            AttachmentText(
                name="archive.zip",
                content_type="application/zip",
                problem="unsupported format",
                sent_on="2026-09-14",
            ),
        ]
    )

    assert (
        "[Attachment 1: facture.pdf (application/pdf), sent on 2026-09-14]\n"
        "Date d'émission : 14 mars 2025\n"
        "[End of attachment 1]"
    ) in context
    assert (
        "[Attachment 2: archive.zip (application/zip), sent on 2026-09-14]" in context
    )
    assert "Not read: unsupported format." in context


def test_format_attachments_context_is_empty_without_attachments():
    """No section when the citizen sent no attachment."""
    assert format_attachments_context([]) == ""

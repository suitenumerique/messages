"""Tests for the blob preview endpoint."""

import uuid

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories
from core.enums import MailboxRoleChoices, PreviewRefusalCode
from core.services.blob_gc import upload_and_reserve_blob

# Minimal but real magic byte sequences for the formats the preview endpoint
# allowlists. Using real bytes (rather than mocking ``magic.from_buffer``)
# proves the validation path end-to-end and matches the convention already
# used by ``tests/importer/test_import_service.py``.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<<>>endobj\n"
    b"xref\n0 1\n0000000000 65535 f\n"
    b"trailer<<>>\n"
    b"startxref\n9\n"
    b"%%EOF\n"
)
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 26 + b"PK\x05\x06" + b"\x00" * 18


@pytest.mark.django_db
class TestBlobPreview:
    """Cover the security contract of GET /api/v1.0/blob/{id}/preview/."""

    @pytest.fixture
    def authed_client(self):
        """Authenticated APIClient and its user."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    @pytest.fixture
    def stranger_client(self):
        """Authenticated APIClient for a user with no mailbox access."""
        user = factories.UserFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    @pytest.fixture
    def mailbox(self, authed_client):
        """Mailbox the authed user can edit (so it can own uploaded blobs)."""
        _, user = authed_client
        mb = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            mailbox=mb, user=user, role=MailboxRoleChoices.EDITOR
        )
        return mb

    @staticmethod
    def _preview_url(blob_id):
        """Build the /api/v1.0/blob/{id}/preview/ URL."""
        return reverse("blob-preview", kwargs={"pk": blob_id})

    def test_preview_png_returns_inline_with_security_headers(
        self, authed_client, mailbox
    ):
        """Allowlisted PNG is served inline with the hardening headers set."""
        client, _ = authed_client
        blob = upload_and_reserve_blob(mailbox, PNG_BYTES, "image/png")

        response = client.get(self._preview_url(blob.id))

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "image/png"
        assert response["Content-Disposition"].startswith("inline;")
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Referrer-Policy"] == "no-referrer"
        csp = response["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "sandbox" in csp
        assert response["Cache-Control"] == "private, max-age=2592000"
        assert response.content == PNG_BYTES

    def test_preview_pdf_returns_200(self, authed_client, mailbox):
        """PDF is in the allowlist and served as application/pdf."""
        client, _ = authed_client
        blob = upload_and_reserve_blob(mailbox, PDF_BYTES, "application/pdf")

        response = client.get(self._preview_url(blob.id))

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "application/pdf"

    def test_preview_zip_refused_415_unsupported(self, authed_client, mailbox):
        """A non-allowlisted MIME (even if declared correctly) must be refused.

        The declared type isn't previewable, so the refusal is reported as
        ``unsupported`` (not suspicious): the client never expected a preview.
        """
        client, _ = authed_client
        blob = upload_and_reserve_blob(mailbox, ZIP_BYTES, "application/zip")

        response = client.get(self._preview_url(blob.id))

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert response.json()["code"] == PreviewRefusalCode.UNSUPPORTED

    def test_preview_mime_mismatch_refused_415_suspicious(self, authed_client, mailbox):
        """ZIP bytes uploaded as image/png must never be served inline.

        The declared type (image/png) was previewable, so a refusal means the
        bytes betrayed the declared type → reported as ``suspicious`` so the UI
        can warn the user instead of showing a blank preview.
        """
        client, _ = authed_client
        # Declared as PNG to pass the allowlist check; magic detection
        # will see ZIP magic bytes and refuse the mismatch.
        blob = upload_and_reserve_blob(mailbox, ZIP_BYTES, "image/png")

        response = client.get(self._preview_url(blob.id))

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert response.json()["code"] == PreviewRefusalCode.SUSPICIOUS

    def test_preview_accepts_declared_type_with_parameters_and_case(
        self, authed_client, mailbox
    ):
        """Declared Content-Type with charset/case variants is normalized.

        Uploads can store ``image/PNG; charset=binary`` verbatim. The preview
        check must compare against the canonical ``image/png`` (parameters
        stripped, lowercased) so valid bytes aren't refused as suspicious.
        """
        client, _ = authed_client
        blob = upload_and_reserve_blob(mailbox, PNG_BYTES, "image/PNG; charset=binary")

        response = client.get(self._preview_url(blob.id))

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "image/png"

    def test_preview_without_access_returns_403(
        self, authed_client, mailbox, stranger_client
    ):
        """Users without mailbox access cannot preview its blobs."""
        _, _ = authed_client
        blob = upload_and_reserve_blob(mailbox, PNG_BYTES, "image/png")

        response = stranger_client.get(self._preview_url(blob.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_preview_unknown_blob_returns_403(self, authed_client):
        """Hide blob existence: unknown UUID must look like a denied blob."""
        client, _ = authed_client

        response = client.get(self._preview_url(uuid.uuid4()))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_preview_malformed_blob_id_returns_400(self, authed_client):
        """Non-UUID, non-``msg_`` pk must surface as 400, not 500."""
        client, _ = authed_client

        response = client.get(self._preview_url("not-a-uuid"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_preview_unauthenticated_returns_401(self, mailbox):
        """Anonymous requests are rejected before any blob lookup."""
        blob = upload_and_reserve_blob(mailbox, PNG_BYTES, "image/png")
        anon = APIClient()

        response = anon.get(self._preview_url(blob.id))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

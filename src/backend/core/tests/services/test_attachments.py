"""Tests for :mod:`core.services.attachments` — the display-name policy."""

import mimetypes

import pytest

from core.models import Attachment
from core.services.attachments import (
    ATTACHMENT_NAME_MAX_LENGTH,
    UNNAMED_ATTACHMENT_STEM,
    get_attachment_display_name,
    guess_mime_extension,
)


def test_name_cap_matches_the_model_field():
    """The truncation limit must be the column's, or saves start failing.

    ``get_attachment_display_name`` truncates so ``full_clean`` never
    rejects a draft over one overlong attachment name. That only holds
    while the constant tracks the field it is protecting.
    """
    assert ATTACHMENT_NAME_MAX_LENGTH == Attachment._meta.get_field("name").max_length


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        pytest.param("image/gif", ".gif", id="image"),
        pytest.param("application/pdf", ".pdf", id="document"),
        pytest.param("text/calendar", ".ics", id="calendar-invite"),
        pytest.param("text/vcard", ".vcf", id="standard-vcard"),
        pytest.param("application/x-zip-compressed", ".zip", id="outlook-zip"),
        pytest.param("application/pkcs7-mime", ".p7m", id="smime-message"),
        pytest.param("application/xml", ".xml", id="xml"),
        # Deliberately extensionless: the generic binary type names no format.
        pytest.param("application/octet-stream", "", id="generic-binary"),
        pytest.param("application/x-unknown", "", id="unknown-type"),
        pytest.param("", "", id="empty"),
        pytest.param(None, "", id="none"),
    ],
)
def test_guess_mime_extension(content_type, expected):
    assert guess_mime_extension(content_type) == expected


def test_guess_mime_extension_drops_parameters():
    assert guess_mime_extension('text/calendar; charset="utf-8"') == ".ics"


def test_guess_mime_extension_is_case_insensitive():
    assert guess_mime_extension("Application/PDF") == ".pdf"


def test_guess_mime_extension_does_not_consult_the_host(monkeypatch):
    """The table is self-contained: no ``mimetypes``, no ``/etc/mime.types``.

    The stdlib table is completed at import time from system files, which
    would make a stored ``Attachment.name`` a property of the host image.
    Poison ``mimetypes`` to prove we never reach it.
    """

    def _explode(*args, **kwargs):
        raise AssertionError("guess_mime_extension consulted the stdlib table")

    monkeypatch.setattr(mimetypes, "guess_extension", _explode)
    monkeypatch.setattr(mimetypes, "init", _explode)

    assert guess_mime_extension("image/png") == ".png"
    assert guess_mime_extension("application/x-unknown") == ""


class TestGetAttachmentDisplayName:
    """A part always resolves to a non-empty, storable name."""

    def test_keeps_a_usable_name(self):
        assert get_attachment_display_name("report.pdf", "application/pdf") == (
            "report.pdf"
        )

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param(None, id="none"),
            pytest.param("", id="empty"),
            # Sanitizes away to nothing — the fallback has to catch this too.
            pytest.param("...", id="sanitizes-to-empty"),
        ],
    )
    def test_synthesizes_a_name_with_the_inferred_extension(self, name):
        assert get_attachment_display_name(name, "image/gif") == "unnamed.gif"

    def test_synthesizes_a_bare_stem_for_an_unknown_type(self):
        assert (
            get_attachment_display_name(None, "application/x-unknown")
            == UNNAMED_ATTACHMENT_STEM
        )

    def test_synthesizes_a_bare_stem_without_a_type(self):
        assert get_attachment_display_name(None) == UNNAMED_ATTACHMENT_STEM

    def test_sanitizes_a_client_supplied_name(self):
        """Names off the wire reach us unvalidated — path traversal included."""
        assert get_attachment_display_name("../../etc/passwd", "text/plain") == (
            "passwd"
        )

    def test_truncates_to_the_column_width_keeping_the_extension(self):
        name = get_attachment_display_name("a" * 300 + ".gif", "image/gif")
        assert len(name) == ATTACHMENT_NAME_MAX_LENGTH
        assert name.endswith(".gif")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(123, id="int"),
        pytest.param(["a.txt"], id="list"),
        pytest.param({"n": "a.txt"}, id="dict"),
        pytest.param(True, id="bool"),
        pytest.param(object(), id="object"),
    ],
)
def test_truthy_non_string_name_falls_back_instead_of_raising(name):
    """``attachments[].name`` reaches us straight off the wire — the draft
    endpoint takes it with no serializer validation — so a client can send
    a number or a list. Those are truthy, and ``sanitize_filename`` slices
    its argument, so a truthiness check let them through to a TypeError and
    a 500. Anything that is not a string is treated as no name at all.
    """
    assert get_attachment_display_name(name, "image/png") == "unnamed.png"


def test_string_names_are_unaffected():
    assert get_attachment_display_name("report.pdf", "application/pdf") == "report.pdf"
    assert get_attachment_display_name("", "image/png") == "unnamed.png"
    assert get_attachment_display_name(None, "image/png") == "unnamed.png"

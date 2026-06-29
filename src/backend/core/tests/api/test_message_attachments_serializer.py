"""Tests for ``MessageSerializer.get_attachments`` name handling."""

from unittest.mock import Mock

import pytest

from core.api.serializers import MessageSerializer


def _serialize_parsed_attachments(parsed_attachments):
    """Run ``get_attachments`` against a non-draft message whose parsed MIME
    exposes ``parsed_attachments``, bypassing DB and ``parse_email``."""
    instance = Mock()
    instance.id = "00000000-0000-0000-0000-000000000000"
    instance.has_attachments = True
    instance.is_draft = False
    instance.get_parsed_field.return_value = parsed_attachments
    return MessageSerializer().get_attachments(instance)


def test_get_attachments_preserves_present_name():
    """A MIME part that carries a ``filename`` keeps it verbatim."""
    result = _serialize_parsed_attachments(
        [{"name": "report.pdf", "size": 12, "type": "application/pdf"}]
    )
    assert [a["name"] for a in result] == ["report.pdf"]


@pytest.mark.parametrize(
    "attachment",
    [
        pytest.param({"size": 1, "type": "text/plain"}, id="missing-name"),
        pytest.param({"name": None, "size": 1, "type": "text/plain"}, id="none-name"),
        pytest.param({"name": "", "size": 1, "type": "text/plain"}, id="empty-name"),
    ],
)
def test_get_attachments_falls_back_to_unnamed(attachment):
    """A MIME part with no usable ``filename`` falls back to the "unnamed"
    sentinel so consumers never receive a null/empty name (regression: a null
    name crashed the frontend calendar-invite download button)."""
    result = _serialize_parsed_attachments([attachment])
    assert result[0]["name"] == "unnamed"

"""Test MessageSummarySerializer."""

import pytest

from core import factories
from core.api.serializers import MessageSummarySerializer


@pytest.mark.django_db
class TestMessageSummarySerializer:
    """MessageSummarySerializer must expose only the lightweight summary fields."""

    def test_serializes_expected_fields(self):
        """Only id/sender/sent_at/is_unread/has_attachments/snippet are exposed."""
        message = factories.MessageFactory(
            snippet="Hello preview",
            has_attachments=True,
        )

        data = MessageSummarySerializer(message).data

        assert set(data.keys()) == {
            "id",
            "sender",
            "sent_at",
            "is_unread",
            "is_draft",
            "has_attachments",
            "snippet",
        }
        assert data["id"] == str(message.id)
        assert data["snippet"] == "Hello preview"
        assert data["has_attachments"] is True
        assert data["sender"]["email"] == message.sender.email

    def test_is_unread_defaults_false_without_annotation(self):
        """Falls back to False when the queryset wasn't annotated with _is_unread."""
        message = factories.MessageFactory()

        data = MessageSummarySerializer(message).data

        assert data["is_unread"] is False

    def test_does_not_touch_blob_storage(self, monkeypatch):
        """Serializing must never call get_parsed_data() (blob fetch + MIME parse)."""
        message = factories.MessageFactory(snippet="Already computed")

        def fail_if_called(*args, **kwargs):
            raise AssertionError("MessageSummarySerializer must not parse the blob")

        monkeypatch.setattr(type(message), "get_parsed_data", fail_if_called)

        serialized = MessageSummarySerializer(message).data  # must not raise
        assert serialized["snippet"] == "Already computed"

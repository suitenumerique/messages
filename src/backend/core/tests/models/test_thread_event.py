"""Tests for ThreadEvent model ASSIGN/UNASSIGN schema validation."""

import uuid

from django.core.exceptions import ValidationError

import pytest

from core import enums, factories

pytestmark = pytest.mark.django_db


class TestThreadEventTypeChoices:
    """Test that ThreadEventTypeChoices contains ASSIGN and UNASSIGN values."""

    def test_assign_value(self):
        """ThreadEventTypeChoices.ASSIGN should equal 'assign'."""
        assert enums.ThreadEventTypeChoices.ASSIGN.value == "assign"

    def test_unassign_value(self):
        """ThreadEventTypeChoices.UNASSIGN should equal 'unassign'."""
        assert enums.ThreadEventTypeChoices.UNASSIGN.value == "unassign"


class TestThreadEventAssignSchema:
    """Test DATA_SCHEMAS validation for ASSIGN and UNASSIGN ThreadEvent types."""

    def test_assign_with_valid_data_passes_clean(self):
        """ThreadEvent type=assign with valid assignees data should pass full_clean()."""
        thread = factories.ThreadFactory()
        author = factories.UserFactory()
        valid_uuid = str(uuid.uuid4())

        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type="assign",
            data={"assignees": [{"id": valid_uuid, "name": "Alice"}]},
        )
        # If we get here without error, the schema validated
        assert event.id is not None

    def test_unassign_with_valid_data_passes_clean(self):
        """ThreadEvent type=unassign with valid assignees data should pass full_clean()."""
        thread = factories.ThreadFactory()
        author = factories.UserFactory()
        valid_uuid = str(uuid.uuid4())

        event = factories.ThreadEventFactory(
            thread=thread,
            author=author,
            type="unassign",
            data={"assignees": [{"id": valid_uuid, "name": "Bob"}]},
        )
        assert event.id is not None

    def test_assign_with_empty_assignees_raises_validation_error(self):
        """ThreadEvent type=assign with empty assignees list should raise ValidationError."""
        thread = factories.ThreadFactory()
        author = factories.UserFactory()

        with pytest.raises(ValidationError):
            factories.ThreadEventFactory(
                thread=thread,
                author=author,
                type="assign",
                data={"assignees": []},
            )

    def test_assign_with_wrong_schema_raises_validation_error(self):
        """ThreadEvent type=assign with content instead of assignees should raise."""
        thread = factories.ThreadFactory()
        author = factories.UserFactory()

        with pytest.raises(ValidationError):
            factories.ThreadEventFactory(
                thread=thread,
                author=author,
                type="assign",
                data={"content": "hello"},
            )

    def test_assign_with_non_uuid_id_raises_validation_error(self):
        """ThreadEvent type=assign with a non-UUID id must be rejected."""
        thread = factories.ThreadFactory()
        author = factories.UserFactory()

        with pytest.raises(ValidationError):
            factories.ThreadEventFactory(
                thread=thread,
                author=author,
                type="assign",
                data={"assignees": [{"id": "not-uuid", "name": "X"}]},
            )

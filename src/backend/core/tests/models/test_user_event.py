"""Tests for UserEvent model."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

import pytest

from core import enums, factories
from core import models as core_models


@pytest.mark.django_db
class TestUserEvent:
    """Test the UserEvent model."""

    def test_user_event_factory_creates_valid_instance(self):
        """UserEventFactory should create a valid UserEvent with all fields."""
        user_event = factories.UserEventFactory()
        assert user_event.id is not None
        assert user_event.user is not None
        assert user_event.thread is not None
        assert user_event.thread_event is not None
        assert user_event.type is not None
        assert user_event.created_at is not None
        assert user_event.updated_at is not None

    def test_user_event_invalid_type_raises_validation_error(self):
        """UserEvent with an invalid type should raise ValidationError on save."""
        with pytest.raises(ValidationError):
            factories.UserEventFactory(type="invalid")

    def test_user_event_multiple_same_type_for_same_user_thread_allowed(self):
        """Multiple UserEvent of the same type for the same (user, thread) are allowed."""
        user = factories.UserFactory()
        thread = factories.ThreadFactory()
        thread_event_1 = factories.ThreadEventFactory(thread=thread)
        thread_event_2 = factories.ThreadEventFactory(thread=thread)

        event_1 = factories.UserEventFactory(
            user=user, thread=thread, thread_event=thread_event_1
        )
        event_2 = factories.UserEventFactory(
            user=user, thread=thread, thread_event=thread_event_2
        )

        assert event_1.id != event_2.id
        assert event_1.user == event_2.user
        assert event_1.thread == event_2.thread
        assert event_1.type == event_2.type

    def test_user_event_read_at_null_by_default(self):
        """UserEvent.read_at should be null by default."""
        user_event = factories.UserEventFactory()
        assert user_event.read_at is None

    def test_user_event_str_representation(self):
        """UserEvent.__str__ should return the expected format."""
        user_event = factories.UserEventFactory()
        expected = (
            f"{user_event.user} - {user_event.type} - "
            f"{user_event.thread} - {user_event.created_at}"
        )
        assert str(user_event) == expected

    def test_user_event_ordering_is_descending_created_at(self):
        """Default ordering should be ['-created_at'] (descending)."""
        assert core_models.UserEvent._meta.ordering == ["-created_at"]

    def test_user_event_cascade_delete_user(self):
        """Deleting a User should cascade-delete related UserEvents."""
        user_event = factories.UserEventFactory()
        user_id = user_event.user.id
        user_event.user.delete()
        assert not core_models.UserEvent.objects.filter(user_id=user_id).exists()

    def test_user_event_cascade_delete_thread(self):
        """Deleting a Thread should cascade-delete related UserEvents."""
        user_event = factories.UserEventFactory()
        thread_id = user_event.thread.id
        user_event.thread.delete()
        assert not core_models.UserEvent.objects.filter(thread_id=thread_id).exists()

    def test_user_event_cascade_delete_thread_event(self):
        """Deleting a ThreadEvent should cascade-delete related UserEvents."""
        user_event = factories.UserEventFactory()
        thread_event_id = user_event.thread_event.id
        user_event.thread_event.delete()
        assert not core_models.UserEvent.objects.filter(
            thread_event_id=thread_event_id
        ).exists()

    def test_user_event_duplicate_via_save_raises_validation_error(self):
        """The unique constraint must reject a duplicate via the normal save path.

        ``BaseModel.save()`` runs ``full_clean()``, so the duplicate is caught
        by Django model validation before hitting the DB.
        """
        user = factories.UserFactory()
        thread_event = factories.ThreadEventFactory()
        factories.UserEventFactory(
            user=user,
            thread=thread_event.thread,
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        with pytest.raises(ValidationError):
            core_models.UserEvent.objects.create(
                user=user,
                thread=thread_event.thread,
                thread_event=thread_event,
                type=enums.UserEventTypeChoices.MENTION,
            )

    def test_user_event_duplicate_via_bulk_create_raises_integrity_error(self):
        """The DB UniqueConstraint must reject duplicates on the bulk_create path.

        ``bulk_create`` bypasses ``full_clean()``, so this exercises the actual
        DB-level constraint. This is the path used by ``sync_mention_user_events``
        and it is what protects against races between two concurrent post_save
        signals on the same ThreadEvent.
        """
        user = factories.UserFactory()
        thread_event = factories.ThreadEventFactory()
        factories.UserEventFactory(
            user=user,
            thread=thread_event.thread,
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        with transaction.atomic(), pytest.raises(IntegrityError):
            core_models.UserEvent.objects.bulk_create(
                [
                    core_models.UserEvent(
                        user=user,
                        thread=thread_event.thread,
                        thread_event=thread_event,
                        type=enums.UserEventTypeChoices.MENTION,
                    )
                ]
            )

    def test_user_event_bulk_create_ignore_conflicts_absorbs_duplicates(self):
        """``bulk_create(..., ignore_conflicts=True)`` must be idempotent.

        This mirrors the signal behavior in ``sync_mention_user_events``: when
        two concurrent flows try to insert the same (user, thread_event, type),
        the second one must be silently absorbed by the UniqueConstraint.
        """
        user = factories.UserFactory()
        thread_event = factories.ThreadEventFactory()
        factories.UserEventFactory(
            user=user,
            thread=thread_event.thread,
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
        )

        core_models.UserEvent.objects.bulk_create(
            [
                core_models.UserEvent(
                    user=user,
                    thread=thread_event.thread,
                    thread_event=thread_event,
                    type=enums.UserEventTypeChoices.MENTION,
                )
            ],
            ignore_conflicts=True,
        )

        assert (
            core_models.UserEvent.objects.filter(
                user=user,
                thread_event=thread_event,
                type=enums.UserEventTypeChoices.MENTION,
            ).count()
            == 1
        )

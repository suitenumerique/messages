"""Tests for ``permanently_delete_messages``, shared by every hard delete."""

from unittest.mock import patch

from django.db import connection

import pytest

from core import factories, models
from core.services import trashbin


def _thread_with(*flags):
    """A thread holding one message per flags dict, its stats up to date."""
    thread = factories.ThreadFactory()
    messages = [factories.MessageFactory(thread=thread, **f) for f in flags]
    thread.update_stats()
    return thread, messages


@pytest.mark.django_db
def test_emptied_thread_goes_and_others_are_reconciled():
    """A thread left without messages is deleted, the others recomputed."""
    emptied, (gone,) = _thread_with({"is_trashed": True})
    kept, (trashed, live) = _thread_with({"is_trashed": True}, {})

    deleted = trashbin.permanently_delete_messages(
        models.Message.objects.filter(pk__in=[gone.pk, trashed.pk])
    )

    assert set(deleted) == {gone.pk, trashed.pk}
    assert not models.Thread.objects.filter(pk=emptied.pk).exists()
    kept.refresh_from_db()
    assert not kept.has_trashed
    assert list(kept.messages.values_list("pk", flat=True)) == [live.pk]


@pytest.mark.django_db(transaction=True)
def test_stats_are_recomputed_outside_the_transaction():
    """Stats are recomputed once the deleted rows are no longer locked."""
    _, (trashed, _) = _thread_with({"is_trashed": True}, {})
    in_atomic_block = []
    update_stats = models.Thread.update_stats

    def recording_update_stats(thread, *args, **kwargs):
        in_atomic_block.append(connection.in_atomic_block)
        return update_stats(thread, *args, **kwargs)

    with patch.object(models.Thread, "update_stats", recording_update_stats):
        trashbin.permanently_delete_messages(
            models.Message.objects.filter(pk=trashed.pk)
        )

    assert in_atomic_block == [False]


@pytest.mark.django_db(transaction=True)
def test_failed_stats_update_keeps_the_deletion():
    """The deletion is committed already; a stats failure is only logged."""
    thread, (trashed, live) = _thread_with({"is_trashed": True}, {})

    with (
        patch.object(models.Thread, "update_stats", side_effect=RuntimeError),
        patch.object(trashbin.logger, "exception") as log_exception,
    ):
        deleted = trashbin.permanently_delete_messages(
            models.Message.objects.filter(pk=trashed.pk)
        )

    assert deleted == [trashed.pk]
    assert list(thread.messages.values_list("pk", flat=True)) == [live.pk]
    log_exception.assert_called_once()

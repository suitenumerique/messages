"""Tests for the trashbin cutoff sweep (``cleanup_trashbin_task``).

The task permanently deletes trashbin items (``is_trashed OR is_spam``) older
than ``TRASHBIN_CUTOFF_DAYS``. Trashed items are aged by ``trashed_at``, spam by
``created_at`` (its receipt time).
"""

from datetime import timedelta

from django.utils import timezone

import pytest

from core import factories, models
from core.services.trashbin import cleanup_trashbin_task

pytestmark = pytest.mark.django_db


def _message(**flags):
    """A message on its own thread, with the given flags."""
    return factories.MessageFactory(raw_mime=b"x" * 200, **flags)


def _age_created_at(message, when):
    """Force created_at (auto_now_add) into the past via a raw UPDATE."""
    models.Message.objects.filter(pk=message.pk).update(created_at=when)


def test_deletes_old_trashed_and_spam(settings):
    """Items past the cutoff are deleted; fresh ones are kept."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    now = timezone.now()
    old = now - timedelta(days=31)
    recent = now - timedelta(days=5)

    old_trashed = _message(is_trashed=True, trashed_at=old)
    old_spam = _message(is_spam=True)
    _age_created_at(old_spam, old)

    fresh_trashed = _message(is_trashed=True, trashed_at=recent)
    fresh_spam = _message(is_spam=True)  # created_at defaults to ~now
    live = _message()

    result = cleanup_trashbin_task()

    assert result == {"deleted_count": 2}
    assert not models.Message.objects.filter(pk=old_trashed.pk).exists()
    assert not models.Message.objects.filter(pk=old_spam.pk).exists()
    assert models.Message.objects.filter(pk=fresh_trashed.pk).exists()
    assert models.Message.objects.filter(pk=fresh_spam.pk).exists()
    assert models.Message.objects.filter(pk=live.pk).exists()


def test_recently_binned_old_message_gets_grace_period(settings):
    """An old message binned recently is aged by trashed_at, not created_at.

    Marking a 60-day-old message as spam/trash today must give it the full
    cutoff window, not delete it on the next run.
    """
    settings.TRASHBIN_CUTOFF_DAYS = 30
    now = timezone.now()
    old = now - timedelta(days=60)
    recent = now - timedelta(days=1)

    # Old message, only just moved to the bin (trashed_at recent).
    trashed = _message(is_trashed=True, trashed_at=recent)
    _age_created_at(trashed, old)
    spam = _message(is_spam=True, trashed_at=recent)
    _age_created_at(spam, old)

    result = cleanup_trashbin_task()

    assert result == {"deleted_count": 0}
    assert models.Message.objects.filter(pk=trashed.pk).exists()
    assert models.Message.objects.filter(pk=spam.pk).exists()


def test_ages_by_created_at_when_trashed_at_missing(settings):
    """Imported/legacy binned rows (NULL trashed_at) still age by created_at."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    # NULL trashed_at (as the importer writes), old created_at.
    imported = _message(is_trashed=True)
    _age_created_at(imported, old)

    assert cleanup_trashbin_task() == {"deleted_count": 1}
    assert not models.Message.objects.filter(pk=imported.pk).exists()


def test_cutoff_zero_disables_the_sweep(settings):
    """TRASHBIN_CUTOFF_DAYS=0 turns automatic deletion off, not "delete all"."""
    settings.TRASHBIN_CUTOFF_DAYS = 0
    ancient = timezone.now() - timedelta(days=3650)

    trashed = _message(is_trashed=True, trashed_at=ancient)
    spam = _message(is_spam=True, trashed_at=ancient)

    assert cleanup_trashbin_task() == {"deleted_count": 0}
    assert models.Message.objects.filter(pk=trashed.pk).exists()
    assert models.Message.objects.filter(pk=spam.pk).exists()


def test_deletes_nothing_when_empty(settings):
    """No trashbin items → a no-op returning zero."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    _message()  # a live message

    assert cleanup_trashbin_task() == {"deleted_count": 0}


def test_emptied_thread_is_removed(settings):
    """A thread whose only message is swept away is deleted with it."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    thread = factories.ThreadFactory()
    factories.MessageFactory(
        thread=thread, raw_mime=b"x" * 200, is_trashed=True, trashed_at=old
    )

    cleanup_trashbin_task()

    assert not models.Thread.objects.filter(pk=thread.pk).exists()

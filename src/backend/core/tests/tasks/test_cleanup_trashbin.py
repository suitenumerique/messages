"""Tests for the trashbin cutoff sweep (``cleanup_trashbin_task``).

The task permanently deletes trashbin items (``is_trashed OR is_spam``) older
than ``TRASHBIN_CUTOFF_DAYS``, aged by ``trashed_at`` with ``created_at`` as the
fallback. Trashed messages expire one by one, spam per conversation once all
of its messages are past the cutoff.
"""

from datetime import timedelta

from django.utils import timezone

import pytest

from core import factories, models
from core.services.trashbin import cleanup_trashbin_task

pytestmark = pytest.mark.django_db


def _message(**flags):
    """A message on its own thread, with the given flags."""
    message = factories.MessageFactory(raw_mime=b"x" * 200, **flags)
    message.thread.update_stats()
    return message


def _thread_message(thread, **flags):
    """A message added to ``thread``; call ``thread.update_stats()`` after."""
    return factories.MessageFactory(thread=thread, raw_mime=b"x" * 200, **flags)


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
    """Legacy binned rows (NULL trashed_at) still age by created_at."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    legacy = _message(is_trashed=True)
    _age_created_at(legacy, old)

    assert cleanup_trashbin_task() == {"deleted_count": 1}
    assert not models.Message.objects.filter(pk=legacy.pk).exists()


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


def test_expired_spam_thread_goes_with_its_unflagged_messages(settings):
    """Spam expires per conversation, as "Empty spam" does.

    Expiring only the spam would leave the clean follow-up behind and bring
    the thread back to the inbox.
    """
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    thread = factories.ThreadFactory()
    spam = _thread_message(thread, is_spam=True, trashed_at=old)
    follow_up = _thread_message(thread)
    _age_created_at(follow_up, old)
    thread.update_stats()

    assert cleanup_trashbin_task() == {"deleted_count": 2}
    assert not models.Message.objects.filter(pk__in=[spam.pk, follow_up.pk]).exists()
    assert not models.Thread.objects.filter(pk=thread.pk).exists()


def test_recent_message_keeps_spam_thread_whole(settings):
    """A spam thread ages by its most recent message, flagged or not.

    A clean reply that just landed in an old spam conversation must not be
    swept away with it.
    """
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    thread = factories.ThreadFactory()
    spam = _thread_message(thread, is_spam=True, trashed_at=old)
    follow_up = _thread_message(thread)  # created_at defaults to ~now
    thread.update_stats()

    assert cleanup_trashbin_task() == {"deleted_count": 0}
    assert models.Message.objects.filter(pk__in=[spam.pk, follow_up.pk]).count() == 2


def test_thread_with_stale_spam_flag_is_not_swept(settings):
    """A spam message alone does not expire its thread.

    ``Thread.is_spam`` left unset by a failed stats update keeps the thread.
    """
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    thread = factories.ThreadFactory()
    spam = _thread_message(thread, is_spam=True, trashed_at=old)
    models.Thread.objects.filter(pk=thread.pk).update(is_spam=False)

    assert cleanup_trashbin_task() == {"deleted_count": 0}
    assert models.Message.objects.filter(pk=spam.pk).exists()


def test_spam_thread_waits_for_its_most_recent_spam(settings):
    """A thread whose latest spam is still within the cutoff is kept whole."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    now = timezone.now()

    thread = factories.ThreadFactory()
    old_spam = _thread_message(
        thread, is_spam=True, trashed_at=now - timedelta(days=40)
    )
    fresh_spam = _thread_message(
        thread, is_spam=True, trashed_at=now - timedelta(days=2)
    )
    thread.update_stats()

    assert cleanup_trashbin_task() == {"deleted_count": 0}
    assert (
        models.Message.objects.filter(pk__in=[old_spam.pk, fresh_spam.pk]).count() == 2
    )


def test_spam_threads_are_swept_across_batches(settings):
    """Batching by thread never leaves a thread half-purged."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=40)

    threads = []
    for _ in range(3):
        thread = factories.ThreadFactory()
        _thread_message(thread, is_spam=True, trashed_at=old)
        _age_created_at(_thread_message(thread), old)
        thread.update_stats()
        threads.append(thread)

    assert cleanup_trashbin_task(batch_size=1) == {"deleted_count": 6}
    assert not models.Thread.objects.filter(pk__in=[t.pk for t in threads]).exists()

"""Tests for the data half of migration 0037 (spam threads, spam bin dates).

Rows are forced into their legacy shape with ``queryset.update()``, which
bypasses ``update_stats`` and ``auto_now`` — the only way to reproduce data
written before ``Thread.is_spam`` covered every message and before the spam
path set ``trashed_at``.

The migration functions are called with the live app registry: their only use
of ``apps`` is ``get_model``, whose signature is the same either way.
"""

import importlib
from datetime import timedelta

from django.apps import apps
from django.utils import timezone

import pytest

from core import factories, models
from core.services.trashbin import cleanup_trashbin_task

# The module name starts with a digit, so it cannot be imported by statement.
migration = importlib.import_module(
    "core.migrations.0037_trashbin_spam_threads_and_sweep_indexes"
)

pytestmark = pytest.mark.django_db


# --- Threads holding spam go to the Spam folder ---


def _legacy_thread(*spam_flags):
    """A thread listed by its first message, one message per flag."""
    thread = factories.ThreadFactory()
    messages = [
        factories.MessageFactory(thread=thread, is_spam=is_spam)
        for is_spam in spam_flags
    ]
    models.Thread.objects.filter(pk=thread.pk).update(is_spam=spam_flags[0])
    return thread, messages


def test_thread_with_a_spam_reply_goes_to_spam():
    """A spam reply no longer hides inside a conversation listed in the inbox."""
    thread, (first, reply) = _legacy_thread(False, True)
    before = timezone.now()

    migration.flag_threads_holding_spam(apps, None)

    thread.refresh_from_db()
    assert thread.is_spam is True
    # Message flags are untouched: the user still sees which message is spam.
    first.refresh_from_db()
    reply.refresh_from_db()
    assert first.is_spam is False
    assert reply.is_spam is True
    # The thread was never shown in the Spam folder: its spam is dated now, so
    # the sweep grants it the full retention period.
    assert reply.trashed_at >= before
    assert first.trashed_at is None


def test_old_conversation_moved_to_spam_survives_the_first_sweep(settings):
    """Nothing the migration moves to the Spam folder is purged right away."""
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = timezone.now() - timedelta(days=90)
    thread, messages = _legacy_thread(False, True)
    models.Message.objects.filter(thread=thread).update(created_at=old, updated_at=old)

    migration.flag_threads_holding_spam(apps, None)
    migration.backfill_spam_trashed_at(apps, None)

    assert cleanup_trashbin_task() == {"deleted_count": 0}
    assert models.Message.objects.filter(thread=thread).count() == len(messages)


@pytest.mark.parametrize(
    "spam_flags,expected",
    [((False, False), False), ((True, False), True), ((True, True), True)],
)
def test_other_threads_keep_their_folder(spam_flags, expected):
    """Threads already listed by the new rule are left as they are."""
    thread, _messages = _legacy_thread(*spam_flags)

    migration.flag_threads_holding_spam(apps, None)

    thread.refresh_from_db()
    assert thread.is_spam is expected
    # Their spam keeps its legacy date, left to the backfill.
    assert not models.Message.objects.filter(
        thread=thread, trashed_at__isnull=False
    ).exists()


# --- Spam gets a trashbin entry date ---


def _message(thread, updated_at, **flags):
    """A message of ``thread`` forced to the given flags and ``updated_at``."""
    message = factories.MessageFactory(thread=thread)
    models.Message.objects.filter(pk=message.pk).update(updated_at=updated_at, **flags)
    return message


def test_spam_gets_the_latest_spam_update_of_its_thread():
    """Every undated spam of a thread shares its most recent spam update."""
    now = timezone.now()
    thread = factories.ThreadFactory()
    old_spam = _message(thread, now - timedelta(days=60), is_spam=True)
    recent_spam = _message(thread, now - timedelta(days=2), is_spam=True)
    # A later non-spam update must not count.
    _message(thread, now, is_spam=False)

    migration.backfill_spam_trashed_at(apps, None)

    old_spam.refresh_from_db()
    recent_spam.refresh_from_db()
    assert old_spam.trashed_at == now - timedelta(days=2)
    assert recent_spam.trashed_at == now - timedelta(days=2)


def test_threads_are_dated_independently():
    """Each thread is dated from its own spam only."""
    now = timezone.now()
    first = _message(factories.ThreadFactory(), now - timedelta(days=10), is_spam=True)
    second = _message(factories.ThreadFactory(), now - timedelta(days=3), is_spam=True)

    migration.backfill_spam_trashed_at(apps, None)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.trashed_at == now - timedelta(days=10)
    assert second.trashed_at == now - timedelta(days=3)


def test_dated_spam_and_other_messages_are_untouched():
    """Existing entry times survive; non-spam rows get no timestamp."""
    now = timezone.now()
    binned_at = now - timedelta(days=5)
    thread = factories.ThreadFactory()
    dated_spam = _message(
        thread, now - timedelta(days=1), is_spam=True, trashed_at=binned_at
    )
    live = _message(thread, now, is_spam=False)
    # Only spam is backfilled: trash keeps its created_at fallback.
    trashed = _message(factories.ThreadFactory(), now, is_trashed=True)

    migration.backfill_spam_trashed_at(apps, None)

    dated_spam.refresh_from_db()
    live.refresh_from_db()
    trashed.refresh_from_db()
    assert dated_spam.trashed_at == binned_at
    assert live.trashed_at is None
    assert trashed.trashed_at is None

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
    # Spam was not dated before the migration (``save`` dates it now).
    models.Message.objects.filter(thread=thread).update(trashed_at=None)
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


def test_thread_whose_only_spam_is_trashed_leaves_spam():
    """Trashed spam no longer holds a thread back, even as its first message."""
    thread, (first, reply) = _legacy_thread(True, False)
    models.Message.objects.filter(pk=first.pk).update(is_trashed=True)
    before_trashed_at = timezone.now() - timedelta(days=3)
    models.Message.objects.filter(pk=first.pk).update(trashed_at=before_trashed_at)

    migration.flag_threads_holding_spam(apps, None)

    thread.refresh_from_db()
    assert thread.is_spam is False
    first.refresh_from_db()
    assert first.is_spam is True
    assert first.trashed_at == before_trashed_at
    reply.refresh_from_db()
    assert reply.trashed_at is None


def test_thread_with_trashed_spam_reply_stays_out_of_spam():
    """A trashed spam reply does not move its thread, nor restart its clock."""
    thread, (_first, reply) = _legacy_thread(False, True)
    trashed_at = timezone.now() - timedelta(days=3)
    models.Message.objects.filter(pk=reply.pk).update(
        is_trashed=True, trashed_at=trashed_at
    )

    migration.flag_threads_holding_spam(apps, None)

    thread.refresh_from_db()
    assert thread.is_spam is False
    reply.refresh_from_db()
    assert reply.trashed_at == trashed_at


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


def test_spam_is_dated_by_its_own_last_update():
    """Every undated spam of a thread gets its own ``updated_at``."""
    now = timezone.now()
    thread = factories.ThreadFactory()
    old_spam = _message(thread, now - timedelta(days=60), is_spam=True)
    recent_spam = _message(thread, now - timedelta(days=2), is_spam=True)

    migration.backfill_spam_trashed_at(apps, None)

    old_spam.refresh_from_db()
    recent_spam.refresh_from_db()
    assert old_spam.trashed_at == now - timedelta(days=60)
    assert recent_spam.trashed_at == now - timedelta(days=2)


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

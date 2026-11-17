"""Tests for migration 0038 (clamp stored timestamps to [year 1, now]).

Bad rows are written with raw SQL: year 0 and year 10000 cannot be built in
Python, and reading such a row back raises, which is the bug being repaired.
The migration function is called with the live app registry, as in the 0037
tests.
"""

import importlib
from datetime import UTC, datetime, timedelta

from django.apps import apps
from django.db import connection
from django.utils import timezone

import pytest

from core import factories, models

migration = importlib.import_module(
    "core.migrations.0038_clamp_out_of_range_timestamps"
)

pytestmark = pytest.mark.django_db

YEAR_10000 = "9999-12-31 23:59:59-05"
YEAR_0 = "0001-01-01 00:00:00+05"


def _set_raw(model, pk, field, literal):
    """Write ``literal`` as a timestamptz, bypassing Python's datetime range."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {model._meta.db_table} SET {field} = %s::timestamptz "
            "WHERE id = %s",
            [literal, pk],
        )


def _thread_with_message():
    thread = factories.ThreadFactory()
    access = factories.ThreadAccessFactory(thread=thread)
    message = factories.MessageFactory(thread=thread)
    return thread, access, message


@pytest.mark.parametrize("literal", [YEAR_10000, YEAR_0])
@pytest.mark.parametrize(
    ("model_name", "field"),
    [
        ("message", "sent_at"),
        ("message", "created_at"),
        *[("thread", f) for f in migration.THREAD_DATES],
        ("threadaccess", "read_at"),
        ("threadaccess", "starred_at"),
    ],
)
def test_unreadable_rows_become_readable(model_name, field, literal):
    """Rows Python cannot read are brought into range."""
    thread, access, message = _thread_with_message()
    obj = {"message": message, "thread": thread, "threadaccess": access}[model_name]
    _set_raw(type(obj), obj.pk, field, literal)
    with pytest.raises(Exception):  # noqa: B017
        type(obj).objects.get(pk=obj.pk)

    before = timezone.now()
    migration.clamp_timestamps(apps, None)

    obj = type(obj).objects.get(pk=obj.pk)
    value = getattr(obj, field)
    if literal == YEAR_0:
        assert value == migration.EARLIEST
    elif field == "sent_at":
        assert value == obj.created_at
    else:
        assert before <= value <= timezone.now()


def test_future_dates_clamped():
    """Future dates (forged Date:, imports, client clocks) become now; sent_at
    goes back to the delivery time."""
    thread, access, message = _thread_with_message()
    delivered = timezone.now() - timedelta(days=2)
    future = timezone.now() + timedelta(days=20)
    models.Message.objects.filter(pk=message.pk).update(
        created_at=delivered, sent_at=future
    )
    imported = factories.MessageFactory(thread=thread)
    models.Message.objects.filter(pk=imported.pk).update(
        created_at=future, sent_at=future
    )
    models.Thread.objects.filter(pk=thread.pk).update(
        messaged_at=future, active_messaged_at=future
    )
    models.ThreadAccess.objects.filter(pk=access.pk).update(
        read_at=future, starred_at=future
    )

    before = timezone.now()
    migration.clamp_timestamps(apps, None)
    after = timezone.now()

    message.refresh_from_db()
    assert message.created_at == delivered
    assert message.sent_at == delivered
    imported.refresh_from_db()
    assert before <= imported.created_at <= after
    assert imported.sent_at == imported.created_at
    thread.refresh_from_db()
    assert before <= thread.messaged_at <= after
    assert before <= thread.active_messaged_at <= after
    # Picked up by ``search_reindex --from-date``.
    assert before <= thread.updated_at <= after
    access.refresh_from_db()
    assert before <= access.read_at <= after
    assert before <= access.starred_at <= after


def _is_unread(message, access):
    message.refresh_from_db()
    access.refresh_from_db()
    return access.read_at is None or message.created_at > access.read_at


def test_unread_future_message_stays_unread():
    """An import read up to a message dated 2099, with one dated 2100 unread:
    once both collapse to now, the unread one is still after the pointer."""
    thread, access, read_msg = _thread_with_message()
    unread_msg = factories.MessageFactory(thread=thread)
    in_2099 = datetime(2099, 1, 1, tzinfo=UTC)
    models.Message.objects.filter(pk=read_msg.pk).update(created_at=in_2099)
    models.Message.objects.filter(pk=unread_msg.pk).update(
        created_at=datetime(2100, 1, 1, tzinfo=UTC)
    )
    models.ThreadAccess.objects.filter(pk=access.pk).update(read_at=in_2099)
    assert _is_unread(unread_msg, access)

    migration.clamp_timestamps(apps, None)

    assert _is_unread(unread_msg, access)
    access.refresh_from_db()
    assert access.read_at <= timezone.now()


def test_read_future_message_stays_read():
    """Without an unread message past the pointer, the thread stays read."""
    _, access, message = _thread_with_message()
    in_2099 = datetime(2099, 1, 1, tzinfo=UTC)
    models.Message.objects.filter(pk=message.pk).update(created_at=in_2099)
    models.ThreadAccess.objects.filter(pk=access.pk).update(read_at=in_2099)
    assert not _is_unread(message, access)

    migration.clamp_timestamps(apps, None)

    assert not _is_unread(message, access)


def test_valid_dates_untouched():
    """Past dates, year 1 and NULLs are left as they are."""
    thread, access, message = _thread_with_message()
    past = datetime(2020, 5, 1, 12, tzinfo=UTC)
    models.Message.objects.filter(pk=message.pk).update(
        created_at=past, sent_at=migration.EARLIEST
    )
    models.ThreadAccess.objects.filter(pk=access.pk).update(
        read_at=past, starred_at=None
    )
    models.Thread.objects.filter(pk=thread.pk).update(
        messaged_at=past, draft_messaged_at=None, updated_at=past
    )

    migration.clamp_timestamps(apps, None)

    message.refresh_from_db()
    assert message.created_at == past
    assert message.sent_at == migration.EARLIEST
    access.refresh_from_db()
    assert access.read_at == past
    assert access.starred_at is None
    thread.refresh_from_db()
    assert thread.messaged_at == past
    assert thread.draft_messaged_at is None
    assert thread.updated_at == past


def test_null_dates_of_a_fixed_row_stay_null():
    """A row fixed for one date keeps its NULL dates NULL (``GREATEST`` and
    ``LEAST`` would have made them now in PostgreSQL)."""
    thread, access, message = _thread_with_message()
    future = timezone.now() + timedelta(days=20)
    models.Thread.objects.filter(pk=thread.pk).update(
        messaged_at=future, draft_messaged_at=None, trashed_messaged_at=None
    )
    models.ThreadAccess.objects.filter(pk=access.pk).update(
        read_at=future, starred_at=None
    )
    models.Message.objects.filter(pk=message.pk).update(created_at=future)
    models.Message.objects.filter(pk=message.pk).update(sent_at=None)

    migration.clamp_timestamps(apps, None)

    thread.refresh_from_db()
    assert thread.draft_messaged_at is None
    assert thread.trashed_messaged_at is None
    access.refresh_from_db()
    assert access.starred_at is None
    message.refresh_from_db()
    assert message.sent_at is None


def test_future_sent_at_of_a_year_0_message():
    """A future ``sent_at`` goes back to ``created_at`` clamped too."""
    _, _, message = _thread_with_message()
    _set_raw(models.Message, message.pk, "created_at", YEAR_0)
    models.Message.objects.filter(pk=message.pk).update(
        sent_at=timezone.now() + timedelta(days=1)
    )

    migration.clamp_timestamps(apps, None)

    message = models.Message.objects.get(pk=message.pk)
    assert message.created_at == migration.EARLIEST
    assert message.sent_at == migration.EARLIEST


def test_dates_within_the_margin_left_alone():
    """A row stored while the migration runs, or by a host whose clock is a
    little ahead, is not clamped."""
    thread, access, message = _thread_with_message()
    soon = timezone.now() + timedelta(minutes=5)
    past = datetime(2020, 5, 1, 12, tzinfo=UTC)
    models.Message.objects.filter(pk=message.pk).update(created_at=soon, sent_at=soon)
    models.Thread.objects.filter(pk=thread.pk).update(messaged_at=soon, updated_at=past)
    models.ThreadAccess.objects.filter(pk=access.pk).update(read_at=soon)

    migration.clamp_timestamps(apps, None)

    message.refresh_from_db()
    assert message.created_at == soon
    assert message.sent_at == soon
    thread.refresh_from_db()
    assert thread.messaged_at == soon
    assert thread.updated_at == past
    access.refresh_from_db()
    assert access.read_at == soon


def test_nothing_to_fix_touches_nothing():
    """With every date in range, no thread is marked for reindex."""
    thread, _, _ = _thread_with_message()
    past = datetime(2020, 5, 1, 12, tzinfo=UTC)
    models.Thread.objects.filter(pk=thread.pk).update(updated_at=past)

    migration.clamp_timestamps(apps, None)

    thread.refresh_from_db()
    assert thread.updated_at == past


@pytest.mark.parametrize(
    ("model_name", "field"),
    [("message", "sent_at"), ("threadaccess", "read_at"), ("thread", "messaged_at")],
)
def test_unreadable_row_marks_its_thread_for_reindex(model_name, field):
    """Each kind of fixed row bumps its thread, even when unreadable."""
    thread, access, message = _thread_with_message()
    other_thread, _, _ = _thread_with_message()
    past = datetime(2020, 5, 1, 12, tzinfo=UTC)
    models.Thread.objects.filter(pk__in=[thread.pk, other_thread.pk]).update(
        updated_at=past
    )
    obj = {"message": message, "thread": thread, "threadaccess": access}[model_name]
    _set_raw(type(obj), obj.pk, field, YEAR_10000)

    before = timezone.now()
    migration.clamp_timestamps(apps, None)

    thread.refresh_from_db()
    other_thread.refresh_from_db()
    assert thread.updated_at >= before
    assert other_thread.updated_at == past

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
    access.refresh_from_db()
    assert before <= access.read_at <= after
    assert before <= access.starred_at <= after


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
        messaged_at=past, draft_messaged_at=None
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

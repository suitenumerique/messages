"""Bring stored timestamps into the range the code now enforces.

Before this release, timestamps taken from senders or clients were stored as
given (issue #855):

- a ``Date:`` in the future went into ``Message.sent_at``, and on imports
  into ``created_at`` too, from which the thread dates and ``read_at`` /
  ``starred_at`` are derived;
- an instant that PostgreSQL accepts but Python cannot represent (year 0 or
  10000 once in UTC, e.g. ``9999-12-31T23:59:59-05:00``) made every later
  read of the row fail. Imports and the flag API could write these.

Future values become the migration time and values before year 1 become
year 1, with a future ``sent_at`` brought back to the message's
``created_at``. The updates run in SQL without loading rows, so the
unreadable ones are fixed too. Clamping the thread dates matches recomputing
them: each one is the latest ``created_at`` of some of its messages, and the
max of clamped values is the clamped max.

The search index sorts messages on ``created_at`` and indexes ``sent_at`` and
the read/starred state, so it keeps the old values of what changes here. The
threads concerned get ``updated_at`` set to the migration time, for
``search_reindex --all --from-date`` to pick them up.
"""

import datetime

from django.db import migrations
from django.db.models import F, Q, Value
from django.db.models.functions import Least
from django.utils import timezone

EARLIEST = datetime.datetime(1, 1, 1, tzinfo=datetime.UTC)

THREAD_DATES = [
    "messaged_at",
    "active_messaged_at",
    "trashed_messaged_at",
    "draft_messaged_at",
    "sender_messaged_at",
    "archived_messaged_at",
]


def _out_of_range(fields, now):
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__gt": now}) | Q(**{f"{field}__lt": EARLIEST})
    return query


def _clamp(model, field, now):
    model.objects.filter(**{f"{field}__gt": now}).update(**{field: now})
    model.objects.filter(**{f"{field}__lt": EARLIEST}).update(**{field: EARLIEST})


def clamp_timestamps(apps, schema_editor):
    """Clamp sender- and client-supplied timestamps to [year 1, now]."""
    Message = apps.get_model("core", "Message")
    Thread = apps.get_model("core", "Thread")
    ThreadAccess = apps.get_model("core", "ThreadAccess")
    now = timezone.now()

    # Collected first, reading ids only: the timestamps may be unreadable.
    thread_ids = (
        set(
            Message.objects.filter(
                _out_of_range(["created_at", "sent_at"], now)
            ).values_list("thread_id", flat=True)
        )
        | set(
            Thread.objects.filter(_out_of_range(THREAD_DATES, now)).values_list(
                "id", flat=True
            )
        )
        | set(
            ThreadAccess.objects.filter(
                _out_of_range(["read_at", "starred_at"], now)
            ).values_list("thread_id", flat=True)
        )
    )

    _clamp(Message, "created_at", now)
    Message.objects.filter(sent_at__gt=now).update(
        sent_at=Least(F("created_at"), Value(now))
    )
    Message.objects.filter(sent_at__lt=EARLIEST).update(sent_at=EARLIEST)
    for field in THREAD_DATES:
        _clamp(Thread, field, now)
    for field in ("read_at", "starred_at"):
        _clamp(ThreadAccess, field, now)

    Thread.objects.filter(id__in=thread_ids).update(updated_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_trashbin_spam_threads_and_sweep_indexes"),
    ]

    operations = [
        migrations.RunPython(clamp_timestamps, migrations.RunPython.noop),
    ]

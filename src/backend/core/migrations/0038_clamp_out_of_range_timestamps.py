"""Bring stored timestamps into the range the code now enforces.

Before this release, timestamps taken from senders or clients were stored as
given:

- a ``Date:`` in the future went into ``Message.sent_at``, and on imports
  into ``created_at`` too, from which the thread dates and ``read_at`` /
  ``starred_at`` are derived;
- an instant that PostgreSQL accepts but Python cannot represent (year 0 or
  10000 once in UTC, e.g. ``9999-12-31T23:59:59-05:00``) made every later
  read of the row fail. Any inbound ``Date:``, imports and the flag API could
  write these.

Values more than an hour in the future become the migration time and values
before year 1 become year 1, with a future ``sent_at`` brought back to the
message's (clamped) ``created_at``. The hour leaves alone the rows stored
while this runs, or by a host whose clock is a little ahead; a value within
it is in the past soon after. The updates run in SQL without loading rows,
so the unreadable ones are fixed too. Clamping the thread dates matches
recomputing them: each one is the latest ``created_at`` of some of its
messages, and the max of clamped values is the clamped max.

The message and thread access dates are not indexed: those two tables are
scanned twice, once to collect the threads concerned and once by a single
``UPDATE``. The thread dates are.

The search index sorts messages on ``created_at`` and indexes ``sent_at`` and
the read/starred state, so it keeps the old values of what changes here. The
threads concerned get ``updated_at`` set to the migration time, for
``search_reindex --all --from-date`` to pick them up.
"""

import datetime

from django.db import migrations
from django.db.models import Case, DateTimeField, F, Q, Value, When
from django.db.models.functions import Greatest, Least
from django.utils import timezone

EARLIEST = datetime.datetime(1, 1, 1, tzinfo=datetime.UTC)
FUTURE_MARGIN = datetime.timedelta(hours=1)

THREAD_DATES = [
    "messaged_at",
    "active_messaged_at",
    "trashed_messaged_at",
    "draft_messaged_at",
    "sender_messaged_at",
    "archived_messaged_at",
]


def _out_of_range(fields, latest):
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__gt": latest}) | Q(**{f"{field}__lt": EARLIEST})
    return query


def _clamped(field, latest, now, *, future=None):
    """``field`` past ``latest`` set to ``now`` (or ``future``), before
    EARLIEST set to EARLIEST, NULL left as is.

    A ``CASE`` rather than ``GREATEST(LEAST(...))``: PostgreSQL's
    ``GREATEST`` and ``LEAST`` ignore NULL, which would turn NULL into now.
    """
    return Case(
        When(**{f"{field}__gt": latest}, then=Value(now) if future is None else future),
        When(**{f"{field}__lt": EARLIEST}, then=Value(EARLIEST)),
        default=F(field),
        output_field=DateTimeField(),
    )


def clamp_timestamps(apps, schema_editor):
    """Clamp sender- and client-supplied timestamps to [year 1, now]."""
    Message = apps.get_model("core", "Message")
    Thread = apps.get_model("core", "Thread")
    ThreadAccess = apps.get_model("core", "ThreadAccess")
    now = timezone.now()
    latest = now + FUTURE_MARGIN

    messages = Message.objects.filter(_out_of_range(["created_at", "sent_at"], latest))
    threads = Thread.objects.filter(_out_of_range(THREAD_DATES, latest))
    accesses = ThreadAccess.objects.filter(
        _out_of_range(["read_at", "starred_at"], latest)
    )

    # Collected first, reading ids only: the timestamps may be unreadable.
    thread_ids = (
        set(messages.values_list("thread_id", flat=True))
        | set(threads.values_list("id", flat=True))
        | set(accesses.values_list("thread_id", flat=True))
    )
    if not thread_ids:
        return

    # SET expressions read the row as it was: ``created_at`` here is the
    # unclamped value, clamped again for ``sent_at``.
    messages.update(
        created_at=_clamped("created_at", latest, now),
        sent_at=_clamped(
            "sent_at",
            latest,
            now,
            future=Greatest(
                Least(F("created_at"), Value(now)),
                Value(EARLIEST),
                output_field=DateTimeField(),
            ),
        ),
    )
    threads.update(**{field: _clamped(field, latest, now) for field in THREAD_DATES})
    accesses.update(
        read_at=_clamped("read_at", latest, now),
        starred_at=_clamped("starred_at", latest, now),
    )
    Thread.objects.filter(id__in=thread_ids).update(updated_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_trashbin_spam_threads_and_sweep_indexes"),
    ]

    operations = [
        migrations.RunPython(clamp_timestamps, migrations.RunPython.noop),
    ]

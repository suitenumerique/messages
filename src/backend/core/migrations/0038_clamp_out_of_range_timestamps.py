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

Values more than an hour ahead become exactly an hour ahead and values
before year 1 become year 1, with a future ``sent_at`` brought back to the
message's (clamped) ``created_at``. The hour leaves alone the rows stored
while this runs, or by a host whose clock is a little ahead, and is in the
past soon after. Clamping to a bound (``min(value, bound)``) keeps the order
between values, so each thread date stays the latest ``created_at`` of its
messages, and a message after ``read_at`` stays after it unless both meet at
the bound, which the ``read_at`` update handles. The updates run in SQL
without loading rows, so the unreadable ones are fixed too.

The message and thread access dates are not indexed: those two tables are
scanned once, to collect the rows concerned, then updated by id. The thread
dates are indexed.

The search index sorts messages on ``created_at`` and indexes ``sent_at`` and
the read/starred state, so it keeps the old values of what changes here. The
threads concerned get ``updated_at`` set to the migration time, for
``search_reindex --all --from-date`` to pick them up.
"""

import datetime

from django.db import migrations
from django.db.models import Case, DateTimeField, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Greatest, Least
from django.utils import timezone

EARLIEST = datetime.datetime(1, 1, 1, tzinfo=datetime.UTC)
FUTURE_MARGIN = datetime.timedelta(hours=1)
ONE_MICROSECOND = datetime.timedelta(microseconds=1)

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


def _clamped(field, latest, *, future=None):
    """``field`` past ``latest`` set to ``latest`` (or ``future``), before
    EARLIEST set to EARLIEST, NULL left as is.

    A ``CASE`` rather than ``GREATEST(LEAST(...))``: PostgreSQL's
    ``GREATEST`` and ``LEAST`` ignore NULL, which would turn NULL into a date.
    """
    return Case(
        When(
            **{f"{field}__gt": latest},
            then=Value(latest) if future is None else future,
        ),
        When(**{f"{field}__lt": EARLIEST}, then=Value(EARLIEST)),
        default=F(field),
        output_field=DateTimeField(),
    )


def clamp_timestamps(apps, schema_editor):
    """Clamp sender- and client-supplied timestamps to [year 1, now + 1h]."""
    Message = apps.get_model("core", "Message")
    Thread = apps.get_model("core", "Thread")
    ThreadAccess = apps.get_model("core", "ThreadAccess")
    now = timezone.now()
    latest = now + FUTURE_MARGIN

    # Collected first, reading ids only: the timestamps may be unreadable. The
    # updates below touch these rows and no other, so every row clamped has
    # its thread marked, even if out-of-range rows are committed meanwhile
    # (by servers still on the previous release); those are left as they are.
    message_rows = list(
        Message.objects.filter(
            _out_of_range(["created_at", "sent_at"], latest)
        ).values_list("id", "thread_id")
    )
    thread_rows = list(
        Thread.objects.filter(_out_of_range(THREAD_DATES, latest)).values_list(
            "id", flat=True
        )
    )
    access_rows = list(
        ThreadAccess.objects.filter(
            _out_of_range(["read_at", "starred_at"], latest)
        ).values_list("id", "thread_id")
    )
    thread_ids = (
        {thread_id for _, thread_id in message_rows}
        | set(thread_rows)
        | {thread_id for _, thread_id in access_rows}
    )
    if not thread_ids:
        return
    messages = Message.objects.filter(id__in=[pk for pk, _ in message_rows])
    threads = Thread.objects.filter(id__in=thread_rows)
    accesses = ThreadAccess.objects.filter(id__in=[pk for pk, _ in access_rows])

    # Before the messages, while their ``created_at`` is the original one. A
    # message unread past the pointer keeps ``created_at > read_at``: when
    # both meet at ``latest``, the pointer goes 1 µs before it. Read messages
    # also at ``latest`` then show unread: with one instant left for all,
    # unread is the order kept, rather than hiding mail.
    unread_clamped = Message.objects.filter(
        thread_id=OuterRef("thread_id"),
        created_at__gt=OuterRef("read_at"),
    ).filter(created_at__gt=latest)
    accesses.update(
        read_at=_clamped(
            "read_at",
            latest,
            future=Case(
                When(Exists(unread_clamped), then=Value(latest - ONE_MICROSECOND)),
                default=Value(latest),
                output_field=DateTimeField(),
            ),
        ),
        starred_at=_clamped("starred_at", latest),
    )
    # SET expressions read the row as it was: ``created_at`` here is the
    # unclamped value, clamped again for ``sent_at``.
    messages.update(
        created_at=_clamped("created_at", latest),
        sent_at=_clamped(
            "sent_at",
            latest,
            future=Greatest(
                Least(F("created_at"), Value(latest)),
                Value(EARLIEST),
                output_field=DateTimeField(),
            ),
        ),
    )
    threads.update(**{field: _clamped(field, latest) for field in THREAD_DATES})
    Thread.objects.filter(id__in=thread_ids).update(updated_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_trashbin_spam_threads_and_sweep_indexes"),
    ]

    operations = [
        migrations.RunPython(clamp_timestamps, migrations.RunPython.noop),
    ]

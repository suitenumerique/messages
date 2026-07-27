"""Spam threads, spam bin dates and the trashbin sweep indexes.

1. A thread is now spam as soon as one of its messages is (see
   ``Thread.update_stats``); it used to mirror the thread's first message alone,
   so a spam reply could sit inside a conversation listed in the inbox and be
   permanently deleted by "Empty spam" without ever having been listed in the
   Spam folder. The flag is recomputed for the threads written under the old
   rule. Message flags are left untouched, but the spam of those threads is
   dated now: the user has never seen them in the Spam folder, so they get the
   full retention period before the sweep may purge them.
2. Spam messages written before the spam path set ``trashed_at`` get one: the
   cutoff sweep would otherwise age them by ``created_at`` and purge spam
   received long ago but reported recently. The real entry time is lost, so it
   is approximated by the latest ``updated_at`` among the spam messages of the
   thread — flagging a message as spam touches it. Spam is purged per thread
   (see ``core.services.trashbin``), so every spam message of a thread gets
   that same date.
3. Partial indexes for the trashbin sweep and "Empty spam": one on the
   expression trash ages by, ``COALESCE(trashed_at, created_at)``, and one
   locating the threads holding spam. Created after the data changes above, so
   those do not maintain them.
"""

import django.db.models.functions.comparison
from django.db import migrations, models
from django.db.models import Exists, Max, OuterRef, Subquery
from django.utils import timezone


def flag_threads_holding_spam(apps, schema_editor):
    """Set ``Thread.is_spam`` on threads holding a spam message.

    Their spam is dated first, while the threads are still told apart by
    ``is_spam=False``.
    """
    Message = apps.get_model("core", "Message")
    Thread = apps.get_model("core", "Thread")
    moving_to_spam = Thread.objects.filter(is_spam=False).filter(
        Exists(Message.objects.filter(thread=OuterRef("pk"), is_spam=True))
    )
    Message.objects.filter(is_spam=True, thread__in=moving_to_spam).update(
        trashed_at=timezone.now()
    )
    moving_to_spam.update(is_spam=True)


def backfill_spam_trashed_at(apps, schema_editor):
    """Set ``trashed_at`` on spam messages that have none."""
    Message = apps.get_model("core", "Message")
    latest_spam_update = (
        Message.objects.filter(thread=OuterRef("thread"), is_spam=True)
        .order_by()
        .values("thread")
        .annotate(latest=Max("updated_at"))
        .values("latest")[:1]
    )
    Message.objects.filter(is_spam=True, trashed_at__isnull=True).update(
        trashed_at=Subquery(latest_spam_update)
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_siteconfiguration"),
    ]

    operations = [
        migrations.RunPython(flag_threads_holding_spam, migrations.RunPython.noop),
        migrations.RunPython(backfill_spam_trashed_at, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                django.db.models.functions.comparison.Coalesce(
                    "trashed_at", "created_at"
                ),
                condition=models.Q(("is_trashed", True)),
                name="msg_trashed_binned_at_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                condition=models.Q(("is_spam", True)),
                fields=["thread"],
                name="msg_spam_thread_idx",
            ),
        ),
    ]

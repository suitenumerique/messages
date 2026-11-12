"""Spam threads, spam bin dates and the trashbin sweep indexes.

1. A thread is now spam as soon as one of its untrashed messages is (see
   ``Thread.update_stats``); it used to mirror the thread's first message alone,
   so a spam reply could sit inside a conversation listed in the inbox and be
   permanently deleted by "Empty spam" without ever having been listed in the
   Spam folder. The flag is recomputed for the threads written under the old
   rule. Message flags are left untouched, but the spam of the threads moving to
   the Spam folder is dated now: the user has never seen it there, so it gets
   the full retention period before the sweep may purge it.
2. Spam messages written before the spam path set ``trashed_at`` get one: the
   cutoff sweep would otherwise age them by ``created_at`` and purge spam
   received long ago but reported recently. The real entry time is lost, so it
   is approximated by the message's ``updated_at`` — flagging a message as spam
   touches it.
3. Partial indexes for the trashbin sweep, on the expression it ages messages
   by, ``COALESCE(trashed_at, created_at)``. Created after the data changes
   above, so those do not maintain them.
"""

import django.db.models.functions.comparison
from django.db import migrations, models
from django.db.models import Exists, F, OuterRef
from django.utils import timezone


def flag_threads_holding_spam(apps, schema_editor):
    """Recompute ``Thread.is_spam`` from the untrashed spam of each thread.

    The spam of threads moving to the Spam folder is dated first, while they
    are still told apart by ``is_spam=False``.
    """
    Message = apps.get_model("core", "Message")
    Thread = apps.get_model("core", "Thread")
    untrashed_spam = Message.objects.filter(
        thread=OuterRef("pk"), is_spam=True, is_trashed=False
    )
    moving_to_spam = Thread.objects.filter(is_spam=False).filter(Exists(untrashed_spam))
    Message.objects.filter(
        is_spam=True, is_trashed=False, thread__in=moving_to_spam
    ).update(trashed_at=timezone.now())
    moving_to_spam.update(is_spam=True)
    Thread.objects.filter(is_spam=True).filter(~Exists(untrashed_spam)).update(
        is_spam=False
    )


def backfill_spam_trashed_at(apps, schema_editor):
    """Set ``trashed_at`` on spam messages that have none."""
    Message = apps.get_model("core", "Message")
    Message.objects.filter(is_spam=True, trashed_at__isnull=True).update(
        trashed_at=F("updated_at")
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
                django.db.models.functions.comparison.Coalesce(
                    "trashed_at", "created_at"
                ),
                condition=models.Q(("is_spam", True)),
                name="msg_spam_binned_at_idx",
            ),
        ),
    ]

"""Index the scan the thread-snippet backfill walks.

``backfill_thread_snippets`` pages through ``snippet="" AND has_messages``
ordered by ``(created_at, id)``. ``messages_thread`` carried no index on
``created_at``, so every batch cost a full sequential scan plus a top-N sort —
the ``LIMIT`` buys nothing without an ordered index.

Partial on the backfill predicate, so it holds only the threads still awaiting
a snippet and shrinks as the backlog clears. Built ``CONCURRENTLY``: no write
lock on a live table.
"""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    """Schema-only migration: one partial index, built without locking."""

    # ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block.
    atomic = False

    dependencies = [
        ("core", "0034_channel_lookup_hash"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="thread",
            index=models.Index(
                fields=["created_at", "id"],
                name="thread_snippet_backfill_idx",
                condition=models.Q(snippet="", has_messages=True),
            ),
        ),
    ]

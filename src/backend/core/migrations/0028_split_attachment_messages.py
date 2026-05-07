"""Backfill ``Attachment.message_id`` from the legacy ``Attachment.messages``
M2M, splitting multi-message rows into per-message rows.

Replaces ``Attachment.messages`` (M2M) semantics with
``Attachment.message`` (FK) — see 0027 for the field add and 0029 for
the M2M drop / NOT NULL flip. The original M2M had no documented
rationale and combined badly with sha-based blob dedup:
``_get_or_create_attachment_from_blob`` keyed on ``(blob, mailbox)`` so
two drafts in the same mailbox attaching the same content shared a
single Attachment row, and sending one of those drafts deleted the row,
silently removing the attachment from the other draft. After this
migration each Attachment belongs to exactly one (draft) Message — see
the ``Attachment`` model docstring.

The data step walks the M2M through table and splits each Attachment
that's linked to N>1 messages into N rows (one per message), keeping
the original row for the first message and cloning for the rest.
Attachments with zero messages are dropped (those would have been
collected by the now-deleted ``delete_orphan_attachments`` management
command).

This step is one-way; reverse migration is unsupported because
recombining per-message rows into a shared M2M doesn't have a
well-defined target (which row's name/cid wins?).

Lives in its own migration to avoid mixing ``RunPython`` row
modifications with the schema cleanup in 0029. Postgres rejects
``ALTER TABLE messages_attachment`` while pending FK trigger events
from earlier ``INSERT/UPDATE/DELETE`` are still buffered in the same
transaction, so the data step must commit on its own first.
"""

from django.db import migrations


def split_multi_message_attachments(apps, schema_editor):
    """Backfill ``Attachment.message_id`` from the old M2M through table.

    For each (attachment, message) pair: the first one updates the
    existing Attachment row in place; subsequent ones clone the row
    so each message ends up with its own. Orphan attachments (no
    messages) are deleted at the end.
    """
    Attachment = apps.get_model("core", "Attachment")
    Through = Attachment.messages.through

    seen = set()
    for through_row in Through.objects.all().iterator():
        att_id = through_row.attachment_id
        msg_id = through_row.message_id
        if att_id not in seen:
            seen.add(att_id)
            Attachment.objects.filter(id=att_id).update(message_id=msg_id)
        else:
            original = Attachment.objects.get(id=att_id)
            original.pk = None
            original.id = None
            original.message_id = msg_id
            original.save()

    Attachment.objects.filter(message_id__isnull=True).delete()


def reverse_unsupported(apps, schema_editor):  # pylint: disable=unused-argument
    raise NotImplementedError(
        "Per-message Attachment FK → shared M2M reverse migration not supported."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_blob_encryption_key_id_blob_storage_location_and_more'),
    ]

    operations = [
        migrations.RunPython(
            split_multi_message_attachments, reverse_unsupported
        ),
    ]

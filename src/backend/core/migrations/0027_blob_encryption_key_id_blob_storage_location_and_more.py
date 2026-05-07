"""Blob tiered storage + lifecycle FK hardening + per-message Attachment FK
+ ``Message.draft_blob`` OneToOneField → ForeignKey + ``MailboxBlob`` model.

Adds ``encryption_key_id`` and ``storage_location`` to Blob, makes
``raw_content`` nullable for object-storage offload, and adds the
``blob_offload_scan_idx`` partial index used by the offload sweeper.

Drops ``Blob.mailbox`` / ``Blob.maildomain`` / ``blob_has_owner`` —
blob lifetime is now governed by the reference graph (Message,
Attachment, MessageTemplate, MailboxBlob), with a periodic GC sweep
(``gc_orphan_blobs_task``). See ``core/services/blob_gc.py`` and
``docs/tiered-storage.md``.

Adds ``MailboxBlob``: the JMAP upload reservation row that protects
a freshly-uploaded blob from GC during the upload-then-attach
window, and proves provenance for the attach-by-id authz check.
Replaces a pre-existing Redis ``SETEX`` reservation primitive with
a real DB row carrying an explicit ``expires_at`` timestamp; the
GC walks ``MailboxBlob`` like any other reference (with the extra
``expires_at > now()`` filter to exclude stale rows) and deletes
expired rows inline before the blob delete (under ``PROTECT``).

Switches ``Attachment.blob``, ``MessageTemplate.blob``,
``Message.blob`` and ``Message.draft_blob`` to ``on_delete=PROTECT``:
the GC is the only authorised deleter of a Blob and always clears
references first under ``select_for_update`` plus the per-sha
advisory lock; PROTECT turns any other code path that tries to
delete a referenced Blob into a loud, recoverable error rather
than a silent CASCADE that destroys an Attachment row, or a
SET_NULL that nulls out a MessageTemplate's body / a Message's
MIME pointer and leaves the operator with bodyless ghosts.

Replaces ``Attachment.messages`` (M2M) with ``Attachment.message``
(FK, ``on_delete=CASCADE``). The original M2M had no documented
rationale and combined badly with sha-based blob dedup:
``_get_or_create_attachment_from_blob`` keyed on ``(blob, mailbox)``
so two drafts in the same mailbox attaching the same content shared
a single Attachment row, and sending one of those drafts deleted
the row, silently removing the attachment from the other draft.
After this migration each Attachment belongs to exactly one
(draft) Message — see the ``Attachment`` model docstring.

The data migration walks the M2M through table and splits each
Attachment that's linked to N>1 messages into N rows (one per
message), keeping the original row for the first message and
cloning for the rest. Attachments with zero messages are dropped
(those would have been collected by the now-deleted
``delete_orphan_attachments`` management command).

Demotes ``Message.draft_blob`` from ``OneToOneField`` to
``ForeignKey`` (drops the UNIQUE constraint on the
``draft_blob_id`` column). The OneToOne combined badly with
sha-based blob dedup: ``BlobManager.create_blob`` returns the
same Blob row for two drafts with identical content, then
OneToOne's uniqueness rejected the second draft's INSERT with an
IntegrityError — easily reproduced by two drafts that share an
empty body. The reverse-relation rename
``Blob.draft`` (single Message) → ``Blob.drafts`` (queryset) is
also encoded; the only consumer is the
``mailbox_usage_metrics`` storage subquery, updated to walk
``drafts__`` instead of ``draft__``.

Dropping the FKs, flipping on_delete, and the M2M→FK / OneToOne→FK
swaps are all metadata-only on Postgres ≥ 11 (Django enforces
on_delete in Python; the DB-level FK clause stays at
``ON DELETE NO ACTION`` regardless). The OneToOne→FK swap drops a
UNIQUE constraint, also catalog-only. The Attachment data step is
one-way; reverse migration is unsupported because recombining
per-message rows into a shared M2M doesn't have a well-defined
target (which row's name/cid wins?).
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


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
        ('core', '0026_userevent_usrevt_user_thread_assign_uniq'),
    ]

    operations = [
        migrations.AddField(
            model_name='blob',
            name='encryption_key_id',
            field=models.SmallIntegerField(default=0, help_text='Encryption key ID (0 = no encryption, >=1 = encrypted with MESSAGES_BLOBS_ENCRYPT_KEYS[str(key_id)])', verbose_name='encryption key ID'),
        ),
        migrations.AddField(
            model_name='blob',
            name='storage_location',
            field=models.SmallIntegerField(choices=[(1, 'PostgreSQL'), (2, 'Object Storage')], default=1, help_text='Where the blob content is stored', verbose_name='storage location'),
        ),
        migrations.AlterField(
            model_name='blob',
            name='raw_content',
            field=models.BinaryField(blank=True, help_text='Compressed binary content of the blob (null if in object storage)', null=True, verbose_name='raw content'),
        ),
        migrations.AddIndex(
            model_name='blob',
            index=models.Index(condition=models.Q(('storage_location', 1)), fields=['created_at'], name='blob_offload_scan_idx'),
        ),
        migrations.RemoveConstraint(
            model_name='blob',
            name='blob_has_owner',
        ),
        migrations.RemoveField(
            model_name='blob',
            name='mailbox',
        ),
        migrations.RemoveField(
            model_name='blob',
            name='maildomain',
        ),
        migrations.AlterField(
            model_name='attachment',
            name='blob',
            field=models.ForeignKey(
                help_text='Reference to the blob containing the attachment data',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='attachments',
                to='core.blob',
            ),
        ),
        migrations.AlterField(
            model_name='messagetemplate',
            name='blob',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Reference to the blob containing template content as JSON: '
                    '{html: str, text: str, raw: any}'
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='message_templates',
                to='core.blob',
            ),
        ),
        migrations.AlterField(
            model_name='message',
            name='blob',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='messages',
                to='core.blob',
            ),
        ),
        migrations.AlterField(
            model_name='message',
            name='draft_blob',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='drafts',
                to='core.blob',
            ),
        ),

        # --- Attachment.messages (M2M) → Attachment.message (FK) --- #
        # 1. Add the FK as nullable, with a temporary related_name to
        # avoid colliding with the M2M's "attachments" reverse on
        # Message during the migration.
        migrations.AddField(
            model_name='attachment',
            name='message',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='attachments_new',
                to='core.message',
                help_text='The draft Message this attachment belongs to',
            ),
        ),
        # 2. Backfill: split multi-message rows, drop orphans.
        migrations.RunPython(
            split_multi_message_attachments, reverse_unsupported
        ),
        # 3. Drop the M2M and its through table; this frees the
        # ``attachments`` related_name on Message.
        migrations.RemoveField(
            model_name='attachment',
            name='messages',
        ),
        # 4. Tighten: NOT NULL, real related_name.
        migrations.AlterField(
            model_name='attachment',
            name='message',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='attachments',
                to='core.message',
                help_text='The draft Message this attachment belongs to',
            ),
        ),

        # --- MailboxBlob: JMAP upload-reservation row --- #
        migrations.CreateModel(
            name='MailboxBlob',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        help_text='primary key for the record as UUID',
                        primary_key=True,
                        serialize=False,
                        verbose_name='id',
                    ),
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        editable=False,
                        help_text='date and time at which a record was created',
                        verbose_name='created on',
                    ),
                ),
                (
                    'updated_at',
                    models.DateTimeField(
                        auto_now=True,
                        editable=False,
                        help_text='date and time at which a record was last updated',
                        verbose_name='updated on',
                    ),
                ),
                (
                    'expires_at',
                    models.DateTimeField(
                        null=True,
                        blank=True,
                        help_text='When the reservation stops protecting the blob from GC. Null or past = stale.',
                    ),
                ),
                (
                    'blob',
                    models.ForeignKey(
                        help_text='The blob whose upload reservation this row holds.',
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='mailbox_uploads',
                        to='core.blob',
                    ),
                ),
                (
                    'mailbox',
                    models.ForeignKey(
                        help_text='The mailbox that uploaded the blob.',
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='blob_uploads',
                        to='core.mailbox',
                    ),
                ),
            ],
            options={
                'verbose_name': 'mailbox blob',
                'verbose_name_plural': 'mailbox blobs',
                'db_table': 'messages_mailboxblob',
                'unique_together': {('blob', 'mailbox')},
            },
        ),
        migrations.AddIndex(
            model_name='mailboxblob',
            index=models.Index(
                fields=['expires_at'], name='mailboxblob_expires_idx'
            ),
        ),
    ]

"""Blob tiered storage + lifecycle FK hardening + ``Message.draft_blob``
OneToOneField → ForeignKey + nullable ``Attachment.message`` + ``MailboxBlob``
model.

Schema-only first half of a 3-part change. The remaining steps are split
to avoid Postgres ``cannot ALTER TABLE because it has pending trigger
events``: combining ``RunPython`` row modifications with later
``ALTER TABLE`` on ``messages_attachment`` in the same transaction
makes deferred FK trigger checks block the trailing schema ops on
COMMIT.

  - 0027 (this file): all schema additions, including
    ``Attachment.message`` as nullable FK alongside the still-present
    ``Attachment.messages`` M2M.
  - 0028: data migration that backfills ``Attachment.message_id`` from
    the M2M and splits multi-message rows.
  - 0029: drops the M2M and tightens the FK to NOT NULL.

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

The new ``Attachment.message`` FK is added with the temporary
related_name ``attachments_new`` to avoid colliding with the M2M's
``attachments`` reverse on Message; 0029 frees that name (by
dropping the M2M) and renames the FK's reverse back to
``attachments``.

Dropping the FKs, flipping on_delete, and the OneToOne→FK swap are
all metadata-only on Postgres ≥ 11 (Django enforces on_delete in
Python; the DB-level FK clause stays at ``ON DELETE NO ACTION``
regardless). The OneToOne→FK swap drops a UNIQUE constraint, also
catalog-only.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


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

        # Add Attachment.message as nullable FK with a temporary related_name
        # to avoid colliding with the M2M's "attachments" reverse on Message.
        # 0028 backfills the column; 0029 drops the M2M and tightens to NOT NULL.
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

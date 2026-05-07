"""Drop ``Attachment.messages`` M2M and tighten ``Attachment.message``
to NOT NULL. Companion to 0027 (FK add) and 0028 (data backfill).

Split out from 0027 because Postgres refuses to ``ALTER TABLE
messages_attachment`` while pending FK trigger events from the 0028
``RunPython`` are still buffered in the transaction. With the data step
in its own migration, those triggers fire at COMMIT before this
migration's ALTER TABLE runs.

Removing the M2M drops the through table and frees the ``attachments``
related_name on Message so the new FK can take it (the FK was added in
0027 with the temporary ``attachments_new`` related_name).

Dropping the M2M and flipping the FK to NOT NULL are metadata-only on
Postgres ≥ 11 — the FK column already exists and is fully populated by
0028, so the NOT NULL flip is a catalog-only constraint addition.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_split_attachment_messages'),
    ]

    operations = [
        # Drop the M2M and its through table; this frees the
        # ``attachments`` related_name on Message.
        migrations.RemoveField(
            model_name='attachment',
            name='messages',
        ),
        # Tighten: NOT NULL, real related_name.
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
    ]

"""Raise the message subject limit from 255 to 998 characters.

Subjects longer than 255 were truncated, which split threads matched on
subject and sent replies with a cut subject. Raising a varchar limit is a
catalog-only change on PostgreSQL: the table is not rewritten.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_trashbin_spam_threads_and_sweep_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='subject',
            field=models.CharField(blank=True, max_length=998, null=True, verbose_name='subject'),
        ),
    ]

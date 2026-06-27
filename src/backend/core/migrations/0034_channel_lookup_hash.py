# Generated for the push-device lookup_hash column.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_channel_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='lookup_hash',
            field=models.CharField(
                blank=True,
                help_text="Hash of the channel's external lookup key.",
                max_length=64,
                null=True,
                verbose_name='lookup hash',
            ),
        ),
        migrations.AddConstraint(
            model_name='channel',
            constraint=models.UniqueConstraint(
                condition=models.Q(('lookup_hash__isnull', False)),
                fields=('lookup_hash',),
                name='uniq_channel_lookup_hash',
            ),
        ),
    ]

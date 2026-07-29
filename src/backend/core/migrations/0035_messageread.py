from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0034_channel_lookup_hash'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageRead',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, help_text='primary key for the record as UUID', primary_key=True, serialize=False, verbose_name='id')),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='date and time at which a record was created', verbose_name='created on')),
                ('updated_at', models.DateTimeField(auto_now=True, help_text='date and time at which a record was last updated', verbose_name='updated on')),
                ('read_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name='read at')),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reads', to='core.message')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_reads', to='core.user')),
            ],
            options={
                'verbose_name': 'message read',
                'verbose_name_plural': 'message reads',
                'db_table': 'messages_messageread',
                'unique_together': {('message', 'user')},
            },
        ),
    ]

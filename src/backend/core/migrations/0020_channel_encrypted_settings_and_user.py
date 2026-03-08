"""Add encrypted_settings and user fields to Channel model."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import encrypted_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_remove_message_read_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="encrypted_settings",
            field=encrypted_fields.fields.EncryptedJSONField(
                blank=True,
                default=dict,
                help_text="Encrypted channel settings (e.g., app-specific passwords)",
                verbose_name="encrypted settings",
            ),
        ),
        migrations.AddField(
            model_name="channel",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text="User who created this channel (used for permissions and auditing)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="channels",
                to=settings.AUTH_USER_MODEL,
                verbose_name="user",
            ),
        ),
    ]

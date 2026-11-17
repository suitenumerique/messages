from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_trashbin_spam_threads_and_sweep_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailboxaccess",
            name="calendar_settings",
            field=models.JSONField(
                blank=True, default=dict, verbose_name="calendar settings"
            ),
        ),
    ]

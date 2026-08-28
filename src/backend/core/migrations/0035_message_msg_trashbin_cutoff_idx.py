from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_channel_lookup_hash"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                condition=models.Q(("is_trashed", True), ("is_spam", True), _connector="OR"),
                fields=["trashed_at", "created_at"],
                name="msg_trashbin_cutoff_idx",
            ),
        ),
    ]

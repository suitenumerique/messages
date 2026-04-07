"""Channel: encrypted_settings + scope_level + user + last_used_at.

Single schema change for this PR.

- Adds ``encrypted_settings`` (EncryptedJSONField) for per-type secrets.
- Adds ``user`` (FK to User, SET_NULL). Set on scope_level=user channels —
  the User the channel is bound to. Otherwise an optional creator-audit
  pointer (the user who created the row via DRF). May be NULL on any
  scope. Personal channels are explicitly deleted by a pre_delete signal
  in core.signals before the User is removed; the FK is SET_NULL rather
  than CASCADE so a future constraint relaxation cannot silently sweep up
  unrelated channels.
- Adds ``scope_level`` (global / maildomain / mailbox / user) with a backfill
  from the existing mailbox/maildomain FKs.
- Adds ``last_used_at`` for operational metadata.
- Drops the ``type`` field's ``default="mta"``: every supported caller
  (DRF serializer, factories, admin) now passes ``type`` explicitly, and
  the implicit default was a bypass for FEATURE_MAILBOX_ADMIN_CHANNELS.
- Replaces the old ``channel_has_target`` XOR constraint with a
  scope-level-driven ``channel_scope_level_targets`` check.
"""

import django.db.models.deletion
import encrypted_fields.fields
from django.conf import settings
from django.db import migrations, models


def backfill_scope_level(apps, schema_editor):
    """Set scope_level on every existing Channel row based on its FKs.

    All existing rows have exactly one of mailbox/maildomain populated (the
    old XOR constraint guaranteed that), so scope_level is derived
    unambiguously.
    """
    Channel = apps.get_model("core", "Channel")
    Channel.objects.filter(mailbox__isnull=False).update(scope_level="mailbox")
    Channel.objects.filter(maildomain__isnull=False).update(scope_level="maildomain")


def noop_reverse(apps, schema_editor):
    """No reverse — scope_level is dropped by AddField reversal."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_threadevent"),
    ]

    operations = [
        # -- drop the implicit "mta" default on Channel.type ------------------
        # The default was a silent FEATURE_MAILBOX_ADMIN_CHANNELS bypass:
        # a nested mailbox/user POST that omitted ``type`` would land an
        # "mta" channel even when "mta" was not in the allowlist. Every
        # supported caller (DRF serializer, factories, admin) now passes
        # ``type`` explicitly; the field is therefore required at the DB
        # layer too, so any future regression fails loudly on INSERT.
        migrations.AlterField(
            model_name="channel",
            name="type",
            field=models.CharField(
                help_text="Type of channel",
                max_length=255,
                verbose_name="type",
            ),
        ),
        # -- encrypted_settings + creator user --------------------------------
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
        # Set on scope_level=user channels — the User the channel is bound
        # to; NULL for any other scope level. SET_NULL rather than CASCADE
        # so a future constraint relaxation can never silently sweep up
        # unrelated channels; user-scope channels are explicitly deleted by
        # the pre_delete signal in core.signals before the User row is
        # removed.
        migrations.AddField(
            model_name="channel",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "User who owns (scope_level=user) or created (audit) "
                    "this channel"
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="channels",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # -- scope_level + last_used_at ---------------------------------------
        migrations.AddField(
            model_name="channel",
            name="scope_level",
            field=models.CharField(
                choices=[
                    ("global", "Global"),
                    ("maildomain", "Maildomain"),
                    ("mailbox", "Mailbox"),
                    ("user", "User"),
                ],
                db_index=True,
                help_text=(
                    "Resource scope the channel is bound to: 'global' "
                    "(instance-wide, no target — admin/CLI only), 'maildomain', "
                    "'mailbox', or 'user' (personal channel bound to ``user``)."
                ),
                max_length=16,
                null=True,
                verbose_name="scope level",
            ),
        ),
        migrations.AddField(
            model_name="channel",
            name="last_used_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    "Operational timestamp updated (throttled) whenever the "
                    "channel is used."
                ),
                null=True,
                verbose_name="last used at",
            ),
        ),
        migrations.RunPython(backfill_scope_level, noop_reverse),
        migrations.AlterField(
            model_name="channel",
            name="scope_level",
            field=models.CharField(
                choices=[
                    ("global", "Global"),
                    ("maildomain", "Maildomain"),
                    ("mailbox", "Mailbox"),
                    ("user", "User"),
                ],
                db_index=True,
                help_text=(
                    "Resource scope the channel is bound to: 'global' "
                    "(instance-wide, no target — admin/CLI only), 'maildomain', "
                    "'mailbox', or 'user' (personal channel bound to ``user``)."
                ),
                max_length=16,
                verbose_name="scope level",
            ),
        ),
        # -- swap XOR check constraint for scope-level-driven one -------------
        migrations.RemoveConstraint(
            model_name="channel",
            name="channel_has_target",
        ),
        migrations.AddConstraint(
            model_name="channel",
            constraint=models.CheckConstraint(
                # ``user`` is permitted on any scope as a creator-audit FK.
                # Only the user-scope clause requires it NOT NULL (target).
                condition=(
                    (
                        models.Q(scope_level="global")
                        & models.Q(mailbox__isnull=True)
                        & models.Q(maildomain__isnull=True)
                    )
                    | (
                        models.Q(scope_level="maildomain")
                        & models.Q(mailbox__isnull=True)
                        & models.Q(maildomain__isnull=False)
                    )
                    | (
                        models.Q(scope_level="mailbox")
                        & models.Q(mailbox__isnull=False)
                        & models.Q(maildomain__isnull=True)
                    )
                    | (
                        models.Q(scope_level="user")
                        & models.Q(mailbox__isnull=True)
                        & models.Q(maildomain__isnull=True)
                        & models.Q(user__isnull=False)
                    )
                ),
                name="channel_scope_level_targets",
            ),
        ),
    ]

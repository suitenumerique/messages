"""Drop the tables left behind by django-celery-beat and django-celery-results.

Both apps were removed when background tasks moved to Dramatiq. Their tables
survive that removal — Django only drops an app's tables if you run
``migrate <app> zero`` *while it is still installed*, which is not something a
single deploy can do. So we clean up explicitly.

Nothing of value is lost. The beat schedule was always defined in code (now
``@cron_task``), and task results moved to Redis. The one exception is a
periodic task an operator added by hand through the beat admin: that row is
deleted here, and it would not have run under Dramatiq anyway. Check for any
before deploying if that is a possibility::

    SELECT name, task, enabled FROM django_celery_beat_periodictask;

The ``django_migrations`` rows go too, and that is what keeps a rollback safe:
with them gone, redeploying the previous release re-applies both apps' initial
migrations and recreates the tables empty, which is all Celery needed to boot
(``DatabaseScheduler`` re-syncs the code-defined schedule on startup). Leaving
the rows behind would instead have left Celery pointed at tables that no longer
exist.
"""

from django.db import migrations

CELERY_TABLES = [
    # Ordered child-first, though CASCADE makes it moot.
    "django_celery_beat_periodictask",
    "django_celery_beat_periodictasks",
    "django_celery_beat_crontabschedule",
    "django_celery_beat_clockedschedule",
    "django_celery_beat_intervalschedule",
    "django_celery_beat_solarschedule",
    "django_celery_results_chordcounter",
    "django_celery_results_groupresult",
    "django_celery_results_taskresult",
]

DROP_TABLES = "\n".join(f'DROP TABLE IF EXISTS "{table}" CASCADE;' for table in CELERY_TABLES)

FORGET_MIGRATIONS = """
DELETE FROM django_migrations
 WHERE app IN ('django_celery_beat', 'django_celery_results');
"""


class Migration(migrations.Migration):
    """Reclaim the schema the Celery apps left behind.

    ``IF EXISTS`` throughout, so this is a no-op on a database that never ran
    Celery (a fresh install, or one already cleaned up).
    """

    dependencies = [
        ("core", "0034_channel_lookup_hash"),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_TABLES + FORGET_MIGRATIONS,
            # Irreversible by design: reversing would have to recreate two
            # third-party schemas we no longer depend on. Rolling back the
            # *code* is what restores them — see the module docstring.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]

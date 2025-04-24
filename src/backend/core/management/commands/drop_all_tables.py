"""Drop all tables in the public schema of the PostgreSQL database."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    """Drop all tables in the public schema of the PostgreSQL database."""

    help = "Drops all tables in the public schema of the PostgreSQL database."

    def handle(self, *args, **options):
        """Drop all tables in the public schema of the PostgreSQL database."""

        # Forbit it in production!
        if settings.ENVIRONMENT == "production":
            self.stdout.write(
                self.style.ERROR("This command is not allowed in production!")
            )
            return

        self.stdout.write("Dropping all tables...")

        with connection.cursor() as cursor:
            cursor.execute("""
                DO $$
                DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                END
                $$;
            """)

        self.stdout.write(self.style.SUCCESS("All tables dropped successfully."))

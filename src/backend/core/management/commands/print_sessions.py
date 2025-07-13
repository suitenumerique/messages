import logging
from importlib import import_module

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    help = "Print active user sessions with optional filters"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Filter sessions by user email (supports partial matching)",
        )
        parser.add_argument(
            "--session-id", type=str, help="Filter by specific session ID"
        )
        parser.add_argument(
            "--verbose", action="store_true", help="Show detailed session data"
        )

    def handle(self, *args, **options):
        redis = get_redis_connection(settings.SESSION_CACHE_ALIAS)
        engine = import_module(settings.SESSION_ENGINE)
        SessionStore = engine.SessionStore

        user_email_filter = options.get("email")
        session_id_filter = options.get("session_id")
        verbose = options.get("verbose", False)

        prefix = ":1:django.contrib.sessions.cache"

        # If session ID filter is provided, check that specific session
        if session_id_filter:
            self._print_specific_session(
                redis, SessionStore, prefix, session_id_filter, verbose
            )
            return

        # Otherwise, iterate through all sessions
        session_count = 0
        filtered_count = 0

        redis_keys = redis.keys(f"{prefix}*")
        self.stdout.write(f"Found {len(redis_keys)} total sessions")

        for redis_key in redis_keys:
            session_count += 1
            session_data = self._get_session_data(redis_key, SessionStore, prefix)
            if not session_data:
                continue

            user, session_key, data = session_data

            # Apply user email filter
            if (
                user_email_filter
                and user_email_filter.lower() not in user.email.lower()
            ):
                continue

            filtered_count += 1
            self._print_session_info(user, session_key, data, verbose)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nProcessed {session_count} sessions, displayed {filtered_count} matching sessions"
            )
        )

    def _get_session_data(self, redis_key, SessionStore, prefix):
        """Extract and validate session data."""
        try:
            # Extract actual session key
            raw_key = redis_key.decode()
            session_key = raw_key.replace(prefix, "")

            # Load and decode session
            session = SessionStore(session_key=session_key)
            data = session.load()

            user_id = data.get("_auth_user_id")
            if not user_id:
                return None

            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                logger.warning(
                    f"User with ID {user_id} not found for session {session_key}"
                )
                return None

            return user, session_key, data

        # pylint: disable=broad-except
        except Exception as e:
            logger.error(f"Failed to process session {redis_key}: {e}")
            return None

    def _print_specific_session(self, redis, SessionStore, prefix, session_id, verbose):
        """Print information for a specific session ID."""
        redis_key = f"{prefix}{session_id}".encode()

        if not redis.exists(redis_key):
            self.stdout.write(
                self.style.ERROR(f"Session with ID '{session_id}' not found")
            )
            return

        session_data = self._get_session_data(redis_key, SessionStore, prefix)
        if not session_data:
            self.stdout.write(
                self.style.ERROR(f"Could not load session data for ID '{session_id}'")
            )
            return

        user, session_key, data = session_data
        self.stdout.write("Session found:")
        self._print_session_info(user, session_key, data, verbose)

    def _print_session_info(self, user, session_key, data, verbose):
        """Print formatted session information."""
        self.stdout.write(
            f"\n{self.style.SUCCESS('User:')} {user.email} "
            f"{self.style.SUCCESS('(ID:')} {user.id}{self.style.SUCCESS(')')} | "
            f"{self.style.SUCCESS('Session Key:')} {session_key}"
        )

        if verbose:
            self.stdout.write("Session Data:")
            for key, value in data.items():
                # Don't log sensitive session data in detail
                if key.startswith("_auth"):
                    self.stdout.write(f"  {key}: [REDACTED]")
                else:
                    self.stdout.write(f"  {key}: {value}")
        else:
            # Show basic session info
            session_info = []
            if "_auth_user_backend" in data:
                session_info.append(f"Backend: {data['_auth_user_backend']}")
            if "_session_expiry" in data:
                session_info.append(f"Expires: {data['_session_expiry']}")

            if session_info:
                self.stdout.write(f"  {' | '.join(session_info)}")

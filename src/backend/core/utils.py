"""Root utils for the core application."""

import html
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from configurations import values

logger = logging.getLogger(__name__)

SNIPPET_MAX_LENGTH = 140


def extract_snippet(parsed_data: dict[str, Any], fallback: str = "") -> str:
    """Extract a text snippet from parsed email/message data.

    Tries textBody first, then htmlBody (stripped of HTML tags).
    Falls back to the provided fallback string if no body content is found.
    Result is truncated to SNIPPET_MAX_LENGTH characters.
    """
    if text_body := parsed_data.get("textBody"):
        return text_body[0].get("content", "")[:SNIPPET_MAX_LENGTH]

    if html_body := parsed_data.get("htmlBody"):
        html_content = html_body[0].get("content", "")
        clean_text = re.sub("<[^>]+>", " ", html_content)
        return " ".join(html.unescape(clean_text).strip().split())[:SNIPPET_MAX_LENGTH]

    return fallback[:SNIPPET_MAX_LENGTH]


class ThreadStatsUpdateDeferrer:
    """
    Manages deferred thread.update_stats() calls.

    Use the context manager to batch multiple delivery status updates
    and trigger a single update_stats() call per affected thread.

    Example:
        with ThreadStatsUpdateDeferrer.defer():
            for recipient in recipients:
                recipient.delivery_status = new_status
                recipient.save()
        # update_stats() called once per affected thread at exit

    Errors during update_stats() are logged but do not propagate,
    ensuring the main logic is not impacted by stats update failures.
    """

    # Set of thread IDs to ensure uniqueness even if the same thread
    # is loaded via different ORM queries within the defer() block
    _deferred_thread_ids: ContextVar[set | None] = ContextVar(
        "deferred_thread_ids", default=None
    )

    @classmethod
    def _get_deferred_thread_ids(cls):
        """Get the set of thread IDs pending stats update, or None if not deferring."""
        return cls._deferred_thread_ids.get()

    @classmethod
    def _set_deferred_thread_ids(cls, thread_ids):
        """Set the deferred thread IDs set."""
        cls._deferred_thread_ids.set(thread_ids)

    @classmethod
    def is_deferred(cls):
        """Check if thread stats updates are currently being deferred."""
        return cls._get_deferred_thread_ids() is not None

    @classmethod
    def defer_for(cls, thread):
        """
        Mark a thread for deferred stats update.

        If deferring is active, adds the thread ID to the deferred set and returns True.
        If not deferring, returns False (caller should update immediately).
        """
        deferred = cls._get_deferred_thread_ids()
        if deferred is not None:
            deferred.add(thread.id)
            return True
        return False

    @classmethod
    @contextmanager
    def defer(cls):
        """
        Context manager to defer thread.update_stats() calls.

        Use this when performing bulk updates that could trigger thread.update_stats()
        multiple times unnecessarily (e.g. updating delivery status of multiple recipients).
        With this context manager, stats will be updated once when exiting the context.

        Supports nested contexts - only the outermost one triggers updates.

        Errors during update_stats() are caught and logged to ensure the main
        logic is not impacted by stats update failures.
        """
        already_deferring = cls.is_deferred()

        if not already_deferring:
            cls._set_deferred_thread_ids(set())

        try:
            yield
        finally:
            if not already_deferring:
                deferred_ids = cls._get_deferred_thread_ids()
                cls._set_deferred_thread_ids(None)

                # Update stats for all affected threads
                # Errors are caught to not impact the main logic
                if deferred_ids:
                    # Import here to avoid circular imports
                    # pylint: disable-next=import-outside-toplevel
                    from core.models import Thread

                    for thread in Thread.objects.filter(id__in=deferred_ids):
                        try:
                            thread.update_stats()
                        # pylint: disable=broad-exception-caught
                        except Exception:
                            logger.exception(
                                "Failed to update stats for thread %s", thread.id
                            )


class JSONValue(values.Value):
    """
    A custom value class based on django-configurations Value class that
    allows to load a JSON string and use it as a value.
    """

    def to_python(self, value):
        """
        Return the python representation of the JSON string.
        """
        return json.loads(value)


class ThrottleRateValue(values.Value):
    """
    A custom value class that parses and validates throttle rate strings
    like "1000/day" at startup.

    Stores the parsed tuple (limit, period_name, period_seconds) or None.
    """

    PERIOD_SECONDS = {
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }

    def to_python(self, value):
        if not value:
            return None

        try:
            limit_str, period = value.split("/")
            limit = int(limit_str)
        except (ValueError, AttributeError) as e:
            raise ValueError(
                f"Invalid throttle rate format '{value}': expected 'number/period' "
                f"(e.g. '1000/day')"
            ) from e

        period = period.lower()
        period_seconds = self.PERIOD_SECONDS.get(period)
        if period_seconds is None:
            raise ValueError(
                f"Invalid throttle period '{period}': must be one of "
                f"{', '.join(self.PERIOD_SECONDS)}"
            )

        return (limit, period, period_seconds)

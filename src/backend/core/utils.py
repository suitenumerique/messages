"""Root utils for the core application."""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Optional

from configurations import values
from django.core.cache import cache
from django.utils import timezone
import dramatiq
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import CurrentMessage
from dramatiq_crontab import cron, interval

logger = logging.getLogger(__name__)


TASK_PROGRESS_CACHE_TIMEOUT = 86400  # 24 hours
TASK_TRACKING_CACHE_TTL = 86400 * 30  # 30 days (matches DRAMATIQ_RESULT_BACKEND TTL)


class EagerBroker(StubBroker):
    """A broker that executes tasks synchronously for testing.
    Equivalent to Celery's CELERY_TASK_ALWAYS_EAGER mode.

    Only runs CurrentMessage and Results middleware (not all middleware, since
    DbConnectionsMiddleware would close DB connections mid-test).
    """

    def enqueue(self, message, *, delay=None):
        from dramatiq.results import Results

        actor = self.get_actor(message.actor_name)
        cm = next((m for m in self.middleware if isinstance(m, CurrentMessage)), None)
        rm = next((m for m in self.middleware if isinstance(m, Results)), None)
        prev = CurrentMessage.get_current_message() if cm else None
        if cm:
            cm.before_process_message(self, message)
        try:
            result = actor.fn(*message.args, **message.kwargs)
            if rm:
                rm.after_process_message(self, message, result=result)
        finally:
            if cm:
                cm.after_process_message(self, message)
                if prev is not None:
                    cm.before_process_message(self, prev)
        return message


class Task:
    """
    Wrapper around Dramatiq Message that provides Celery-like API.
    """
    def __init__(self, message):
        self._message = message

    @property
    def id(self):
        """Celery-compatible task ID (maps to message_id)."""
        return self._message.message_id

    def track_owner(self, user_id):
        """Register tracking metadata for permission checks and result retrieval."""
        cache.set(f"task_tracking:{self.id}", json.dumps({
            "owner": str(user_id),
            "actor_name": self._message.actor_name,
            "queue_name": self._message.queue_name,
        }), timeout=TASK_TRACKING_CACHE_TTL)

    def __getattr__(self, name):
        """Delegate any other attributes to the underlying message."""
        return getattr(self._message, name)


class CeleryCompatActor(dramatiq.Actor):
    """
    A custom actor class that adds Celery-compatible methods to Dramatiq actors.
    This allows keeping the .delay() API throughout the codebase.
    """
    def delay(self, *args, **kwargs):
        message = self.send(*args, **kwargs)
        return Task(message)


def register_task(*args, **kwargs):
    """
    Decorator to register a dramatiq actor.
    Use this instead of @dramatiq.actor to abstract away the dependency.
    """
    kwargs.setdefault("store_results", True)
    if "queue" in kwargs:
        kwargs.setdefault("queue_name", kwargs.pop("queue"))
    kwargs.setdefault("actor_class", CeleryCompatActor)

    def decorator(fn):
        return dramatiq.actor(fn, **kwargs)

    if args and callable(args[0]):
        return decorator(args[0])
    return decorator


def cron_task(*args, **kwargs):
    """
    Decorator to register a cron task.
    Use this instead of @cron to abstract away the dependency.
    
    Supports:
    - cron_task("*/5 * * * *") - standard cron expression
    - cron_task(interval=300) - run every 300 seconds
    """
    if "interval" in kwargs:
        return interval(seconds=kwargs.pop("interval"))
    return cron(*args, **kwargs)


def get_task_tracking(task_id: str) -> Optional[Dict[str, str]]:
    """Get tracking metadata for a task, or None if not found."""
    raw = cache.get(f"task_tracking:{task_id}")
    if raw is None:
        return None
    return json.loads(raw)


def set_task_progress(progress: int, metadata: Optional[Dict[str, Any]] = None) -> None:
    """
    Set the progress of the currently executing task.
    """
    current_message = CurrentMessage.get_current_message()

    if not current_message:
        logger.warning("set_task_progress called outside of a dramatiq actor")
        return

    task_id = current_message.message_id
    try:
        progress = max(0, min(100, int(progress)))
    except (TypeError, ValueError):
        progress = 0

    progress_data = {
        "progress": progress,
        "timestamp": timezone.now().timestamp(),
        "metadata": metadata or {},
    }

    cache_key = f"task_progress:{task_id}"
    cache.set(cache_key, progress_data, timeout=TASK_PROGRESS_CACHE_TIMEOUT)


def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the progress of a task by ID.
    """
    cache_key = f"task_progress:{task_id}"
    return cache.get(cache_key)


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

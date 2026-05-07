"""Root utils for the core application."""

import html
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ValidationError

import jsonschema
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


def validate_json_schema(value, schema, *, field):
    """Validate ``value`` against ``schema`` and raise a Django ValidationError
    keyed by ``field`` when it does not match.

    A ``FormatChecker`` is always supplied so JSON Schema ``format`` keywords
    (e.g. ``uuid``) are actually enforced — without it they are annotation-only
    and invalid values slip through silently.
    """
    try:
        jsonschema.validate(value, schema, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as exception:
        raise ValidationError({field: exception.message}) from exception


class AbstractBatchingDeferrer:
    """
    Base class for scoped batching of deferred actions via ContextVar.
    Do not use this class directly; implement a subclass instead.

    Subclasses must implement `_flush(items)` method with the action to run once on the
    outermost `defer()` exit with the set of collected items. Nested
    `defer()` calls reuse the outermost scope's collection; only the
    outermost exit flushes.

    Subclasses must set `_context_var_name` class attribute.
    A fresh ContextVar is bound per subclass via `__init_subclass__`
    so that subclasses don't share state.
    """

    _context_var_name: str = ""
    _deferred_items: ContextVar[set | None]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = cls._context_var_name or f"batching_deferrer:{cls.__name__}"
        cls._deferred_items = ContextVar(name, default=None)

    @classmethod
    def is_deferred(cls) -> bool:
        """Return whether a `defer()` scope is currently active."""
        return cls._deferred_items.get() is not None

    @classmethod
    def defer_item(cls, item) -> bool:
        """Add `item` to the current scope; return True if a scope is active.

        Returns True when the caller must skip the immediate action (the item
        is batched). Returns False when no scope is active (caller acts
        immediately).
        """
        items = cls._deferred_items.get()
        if items is None:
            return False
        if item is not None:
            items.add(item)
        return True

    @classmethod
    @contextmanager
    def defer(cls):
        """Collect items during the scope; flush once on outermost exit."""
        is_deferring = cls.is_deferred()

        if not is_deferring:
            cls._deferred_items.set(set())

        try:
            yield
        finally:
            if not is_deferring:
                items = cls._deferred_items.get()
                cls._deferred_items.set(None)
                if items:
                    cls._flush(items)

    @classmethod
    def _flush(cls, items: set) -> None:
        """Subclass hook called with collected items on outermost scope exit."""
        raise NotImplementedError


class ThreadStatsUpdateDeferrer(AbstractBatchingDeferrer):
    """Batch `Thread.update_stats()` calls.

    Example:
        with ThreadStatsUpdateDeferrer.defer():
            for recipient in recipients:
                recipient.delivery_status = new_status
                recipient.save()
        # One update_stats() call per affected thread at scope exit.

    Per-thread update errors are logged; the main logic is never impacted.
    """

    _context_var_name = "deferred_thread_stats_ids"

    # Cap the SQL ``IN`` clause and bound the resultset materialized per
    # query. Without chunking, a large bulk import (>10k unique threads)
    # would issue a single ``filter(id__in=[…])`` whose payload and planner
    # cost grow with the input size.
    STATS_FLUSH_BATCH_SIZE = 500

    @classmethod
    def _flush(cls, items):
        # Lazy import: this module is loaded by settings.py (for JSONValue /
        # ThrottleRateValue), so importing Django models at module level
        # would hit AppRegistryNotReady before the apps finish loading.
        # pylint: disable-next=import-outside-toplevel
        from core.models import Thread

        item_list = list(items)
        for start in range(0, len(item_list), cls.STATS_FLUSH_BATCH_SIZE):
            chunk_ids = item_list[start : start + cls.STATS_FLUSH_BATCH_SIZE]
            for thread in Thread.objects.filter(id__in=chunk_ids):
                try:
                    thread.update_stats()
                # pylint: disable=broad-exception-caught
                except Exception:
                    logger.exception("Failed to update stats for thread %s", thread.id)


class ThreadReindexDeferrer(AbstractBatchingDeferrer):
    """Batch OpenSearch thread reindex enqueues within a scope.

    When active, signal handlers collect thread IDs instead of enqueuing one
    Celery task per row. On outermost scope exit, collected IDs are sliced
    into chunks of ``settings.SEARCH_FLUSH_BATCH_SIZE`` and one
    ``bulk_reindex_threads_task`` is enqueued per chunk — avoiding broker
    saturation and worker churn during bulk delivery flows (imports,
    migrations, …) while bounding each task's payload to match the cap
    applied by ``process_pending_reindex``.

    Example:
        with ThreadReindexDeferrer.defer():
            for email in large_mailbox:
                deliver_inbound_message(...)
        # One or more bulk_reindex_threads_task.delay(chunk) at scope exit,
        # each chunk capped at SEARCH_FLUSH_BATCH_SIZE thread IDs.
    """

    _context_var_name = "deferred_reindex_thread_ids"

    @classmethod
    def _flush(cls, items):
        # Lazy imports: this module is loaded by settings.py (for JSONValue /
        # ThrottleRateValue), and the search modules pull in Django models
        # and the Celery app — both would fail at top-level import time.
        # pylint: disable-next=import-outside-toplevel
        from django.conf import settings

        # pylint: disable-next=import-outside-toplevel
        from core.services.search.coalescer import enqueue_thread_reindex

        # pylint: disable-next=import-outside-toplevel
        from core.services.search.tasks import bulk_reindex_threads_task

        batch_size = settings.SEARCH_FLUSH_BATCH_SIZE
        thread_ids = [str(tid) for tid in items]
        for start in range(0, len(thread_ids), batch_size):
            chunk = thread_ids[start : start + batch_size]
            try:
                bulk_reindex_threads_task.delay(chunk)
            # pylint: disable=broad-exception-caught
            except Exception:
                logger.exception(
                    "Failed to enqueue bulk_reindex_threads_task for %d threads; "
                    "returning them to the pending reindex set for retry",
                    len(chunk),
                )
                for tid in chunk:
                    enqueue_thread_reindex(tid)


class JSONValue(values.Value):
    """
    A custom value class based on django-configurations Value class that
    allows to load a JSON string and use it as a value.
    """

    def to_python(self, value):
        """Return the python representation of the JSON string.

        On parse failure, the original input is suppressed (``raise
        ... from None``) so its contents — which may be a secret, e.g.
        ``MESSAGES_BLOBS_ENCRYPT_KEYS`` — never surface in
        tracebacks, Sentry breadcrumbs, or pod logs.
        """
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            name = getattr(self, "environ_name", None) or "<JSONValue>"
            raise ImproperlyConfigured(f"{name} is not valid JSON") from None


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

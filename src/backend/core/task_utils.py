"""Background-task API — the single seam between app code and Dramatiq.

Application code never imports ``dramatiq`` directly: it declares tasks with
``@register_task`` (optionally scheduled with ``@cron_task``) and dispatches
them with ``.delay()`` / ``.apply_async()``. Everything the queue library
exposes that we actually use is re-exported here, so swapping the broker — or
the library — touches this module and nothing else.

The dispatch API is deliberately Celery-shaped (``.delay()``, ``.apply_async()``,
``task.id``, seconds rather than milliseconds) because that is what the whole
codebase was written against. Dramatiq's native ``.send()`` /
``.send_with_options()`` stay available on every actor for new code.

Notable differences from the Celery setup this replaces:

- **Retries are opt-in.** Dramatiq retries *every* failing task 20 times by
  default; Celery retried none. ``register_task`` defaults ``max_retries=0`` so
  the migration changes no behaviour, and tasks that want retries ask for them
  (``max_retries=``/``retry_on=``), which is checked against the same exception
  tuples Celery's ``autoretry_for`` used.
- **One queue per task.** Dramatiq binds a queue to the actor instead of
  matching module globs at dispatch time, so the routing that used to live in
  ``CELERY_TASK_ROUTES`` is now the ``queue=`` argument on each task.
- **One time limit.** Dramatiq has no soft/hard split: ``time_limit`` raises
  ``TaskTimeLimitExceeded`` inside the task, exactly like Celery's *soft* limit.
  Tasks that need to clean up on timeout catch it.
"""

import json
import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

import dramatiq
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import CurrentMessage
from dramatiq.middleware.time_limit import TimeLimitExceeded as TaskTimeLimitExceeded

logger = logging.getLogger(__name__)

__all__ = [
    "TaskTimeLimitExceeded",
    "cron_task",
    "current_task_id",
    "current_task_retries",
    "get_task_progress",
    "get_task_tracking",
    "on_worker_shutdown",
    "register_task",
    "register_task_owner",
    "set_task_progress",
]

# How long "who started this task" and the progress crumbs live. Both gate the
# task-status endpoint, which is only ever polled while a user waits on a
# spinner, so a day is generous.
TASK_TRACKING_CACHE_TTL = 86400  # 24 hours
TASK_PROGRESS_CACHE_TTL = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------

#: Every queue, mapped to the priority tasks on it get by default.
#:
#: Dramatiq prioritises per *actor*, not per queue: a worker feeds every queue
#: it consumes into one shared priority queue, and takes the lowest number
#: first (ties broken FIFO). Listing queues in a given order to ``--queues``
#: does nothing on its own — so each queue's rank is stamped onto its tasks
#: here, which is what actually makes an inbound message beat a reindex when
#: both are waiting in the same worker.
#:
#: This only orders work *already reserved by one process*. Keeping a queue
#: from being starved outright is a matter of giving it its own worker (see
#: the Procfile), not of priority.
QUEUE_PRIORITIES = {
    "inbound": 0,  # Inbound mail: time-sensitive, everything else can wait
    "outbound": 10,  # Outbound delivery
    "default": 20,  # Webhooks, push, calendar, exports, housekeeping
    "imports": 30,  # Long, sequential, resumable
    "blobs": 40,  # Hourly GC / offload sweeps
    "reindex": 50,  # Search indexing: the most deferrable work there is
}

#: Queue names, highest priority first.
ALL_QUEUES = list(QUEUE_PRIORITIES)

#: Queues whose tasks are long enough that a worker must consume them alone.
#: A message's recovery deadline runs from when the broker *delivers* it, so a
#: long task strands whatever is reserved beside it — one message per sibling
#: queue — until it is reclaimed elsewhere and runs a second time.
LONG_RUNNING_QUEUES = ["blobs", "imports", "reindex"]

#: Tasks that outlive the default time limit yet stay on a shared queue,
#: knowingly. The distinction is chronic versus occasional: the blob sweeps run
#: ~55 minutes every hour and would strand neighbours permanently, hence
#: ``blobs``; these are an operator action and two normally-quick sweeps, so
#: the cost is a rare duplicate run that the neighbour's own idempotency
#: absorbs. ``test_worker.py`` fails on any *new* long task outside this set.
SHARED_LONG_TASKS = {
    "retry_messages_task",
    "purge_abandoned_inbound_messages_task",
    "export_mailbox_task",
}

#: Dramatiq's in-worker limit for a task that declares none — and the point
#: past which the broker treats a message as abandoned.
DEFAULT_TIME_LIMIT_MS = 600_000


# ---------------------------------------------------------------------------
# Declaring tasks
# ---------------------------------------------------------------------------


class Task:
    """A dispatched task — Dramatiq's ``Message`` with a Celery-shaped surface.

    Only ``.id`` and ``.track_owner()`` are ours; anything else falls through
    to the underlying ``dramatiq.Message``.
    """

    def __init__(self, message):
        self._message = message

    @property
    def id(self):
        """The task id, as returned to API clients (Dramatiq's message_id)."""
        return self._message.message_id

    def track_owner(self, user_id):
        """Record who started this task so they — and only they — can poll it."""
        register_task_owner(
            self.id,
            user_id,
            actor_name=self._message.actor_name,
            queue_name=self._message.queue_name,
        )

    def __getattr__(self, name):
        return getattr(self._message, name)

    def __repr__(self):
        return f"<Task {self._message.actor_name} {self.id}>"


class CompatActor(dramatiq.Actor):
    """Actor with the Celery dispatch methods the codebase is written against."""

    def delay(self, *args, **kwargs):
        """Enqueue the task with the given arguments."""
        return Task(self.send(*args, **kwargs))

    def apply_async(
        self,
        args=None,
        kwargs=None,
        task_id=None,
        countdown=None,
        eta=None,
        **options,
    ):
        """Enqueue the task, optionally deferred and/or under a chosen id.

        ``task_id`` lets a caller mint the id *before* dispatch — used when the
        id must be returned to an HTTP client while the actual enqueue is
        deferred to ``transaction.on_commit``.
        """
        delay = None
        if countdown is not None:
            delay = max(0, int(countdown * 1000))
        elif eta is not None:
            delay = max(0, int((eta - timezone.now()).total_seconds() * 1000))

        if task_id is None:
            message = self.send_with_options(
                args=tuple(args or ()),
                kwargs=dict(kwargs or {}),
                delay=delay,
                **options,
            )
        else:
            message = self.message_with_options(
                args=tuple(args or ()), kwargs=dict(kwargs or {}), **options
            ).copy(message_id=str(task_id))
            message = self.broker.enqueue(message, delay=delay)

        return Task(message)


def _seconds_to_ms(value):
    """Our API takes seconds (like Celery); Dramatiq wants milliseconds."""
    return None if value is None else int(value * 1000)


def register_task(
    fn=None,
    *,
    queue="default",
    time_limit=None,
    max_retries=0,
    retry_on=None,
    min_backoff=1,
    max_backoff=600,
    store_results=True,
    **options,
):
    """Register a function as a background task.

    All durations are in **seconds**.

    Args:
        queue: queue the task runs on — one of ``ALL_QUEUES``. Also sets the
            task's priority, from ``QUEUE_PRIORITIES``; pass ``priority=``
            to rank a task above or below its queue's other tasks.
        time_limit: raise ``TaskTimeLimitExceeded`` inside the task after this
            long. Defaults to Dramatiq's 10 minutes.
        max_retries: how many times to re-run the task after a failure.
            ``0`` (the default) means a failure dead-letters immediately, which
            is what the Celery setup did.
        retry_on: exception classes that are worth retrying — the equivalent of
            Celery's ``autoretry_for``. Anything else dead-letters at once even
            when ``max_retries`` is set. ``None`` retries on any exception.
        min_backoff / max_backoff: bounds of the exponential (jittered) delay
            between retries.
        store_results: keep the return value in the result backend so the task
            can be polled through the task-status endpoint.
    """
    if retry_on is not None:
        if not max_retries:
            # Otherwise the task would advertise a retry policy and quietly
            # dead-letter on the first failure anyway.
            raise ValueError("retry_on= has no effect without max_retries=")

        exc_types = tuple(retry_on)

        def retry_when(retries, exception, _exc_types=exc_types, _max=max_retries):
            return retries < _max and isinstance(exception, _exc_types)

        options["retry_when"] = retry_when

    if queue not in QUEUE_PRIORITIES:
        # A typo here would otherwise declare a queue no worker consumes, and
        # the task would simply never run — silently.
        raise ValueError(
            f"unknown queue {queue!r}; expected one of {', '.join(ALL_QUEUES)}"
        )

    options.setdefault("priority", QUEUE_PRIORITIES[queue])
    options.update(
        {
            "queue_name": queue,
            "actor_class": CompatActor,
            "max_retries": max_retries,
            "min_backoff": _seconds_to_ms(min_backoff),
            "max_backoff": _seconds_to_ms(max_backoff),
            "store_results": store_results,
        }
    )
    if time_limit is not None:
        options["time_limit"] = _seconds_to_ms(time_limit)

    def decorator(func):
        return dramatiq.actor(func, **options)

    return decorator(fn) if fn is not None else decorator


def cron_task(*, crontab=None, interval=None):
    """Schedule a task periodically. Apply it *above* ``@register_task``::

        @cron_task(crontab="*/5 * * * *")
        @register_task(queue="outbound")
        def retry_messages_task():
            ...

    Exactly one of:
        crontab: a five-field cron expression, run on wall-clock boundaries.
            The day-of-week field must be literal (``Mon``…``Sun``) or ``*``.
        interval: seconds between runs, counted from scheduler start. Use this
            when the period comes from a setting rather than a constant.

    The schedule is only *acted on* by the scheduler process (``python
    worker.py`` without ``--disable-scheduler``, i.e. ``manage.py crontab``),
    which holds a Redis lock so exactly one runs across the fleet. Registering
    it in web/worker processes is inert.
    """
    if (crontab is None) == (interval is None):
        raise ValueError("cron_task() takes exactly one of crontab= or interval=")

    def decorator(actor):
        if settings.DISABLE_TASK_SCHEDULE:
            return actor

        # Imported lazily: pulls in APScheduler, which the web process and the
        # test suite have no reason to load.
        # pylint: disable-next=import-outside-toplevel
        import dramatiq_crontab

        _configure_scheduler()

        if crontab is not None:
            return dramatiq_crontab.cron(crontab)(actor)
        return dramatiq_crontab.interval(seconds=interval)(actor)

    return decorator


@lru_cache(maxsize=None)
def _configure_scheduler():
    """Widen APScheduler's misfire window, once, before any job is registered.

    A job whose fire time slips past ``misfire_grace_time`` is not run late —
    it is *dropped*, with nothing but a log line. The default is one second, so
    a brief stall in the scheduler process (a slow Redis round-trip, a starved
    dyno) silently skips a tick: no retry sweep for five minutes, or no nightly
    purge until tomorrow. Minutes of grace cost nothing, ``coalesce`` keeps a
    backlog from firing the same job repeatedly once the scheduler catches up,
    and ``max_instances`` keeps a slow job from overlapping itself.

    Safe to call before jobs are added and only then: ``configure()`` resets the
    executor and job store (both recreated on ``start()``) but leaves jobs
    already queued for registration alone.
    """
    # pylint: disable-next=import-outside-toplevel
    import dramatiq_crontab

    dramatiq_crontab.scheduler.configure(
        job_defaults={"misfire_grace_time": 300, "coalesce": True, "max_instances": 1}
    )


# ---------------------------------------------------------------------------
# Talking to the task you are inside of
# ---------------------------------------------------------------------------


def current_task_id() -> Optional[str]:
    """Id of the task currently executing, or None outside a task."""
    message = CurrentMessage.get_current_message()
    return message.message_id if message else None


def current_task_retries() -> int:
    """How many times the current task has already been retried."""
    message = CurrentMessage.get_current_message()
    return message.options.get("retries", 0) if message else 0


def set_task_progress(progress: int, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Publish progress (0-100) for the running task, for the status endpoint.

    Replaces Celery's ``self.update_state(state="PROGRESS", meta=...)``. Called
    outside a task it logs and returns — callers routinely run the same code
    path synchronously (management commands, tests).
    """
    task_id = current_task_id()
    if task_id is None:
        logger.debug("set_task_progress called outside of a task, ignoring")
        return

    try:
        progress = max(0, min(100, int(progress)))
    except (TypeError, ValueError):
        progress = 0

    cache.set(
        f"task_progress:{task_id}",
        {
            "progress": progress,
            "timestamp": timezone.now().timestamp(),
            "metadata": metadata or {},
        },
        timeout=TASK_PROGRESS_CACHE_TTL,
    )


def get_task_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """Last progress published by a task, or None if it never published any."""
    return cache.get(f"task_progress:{task_id}")


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------

_shutdown_callbacks = []


def on_worker_shutdown(fn):
    """Register a callback to run when a worker process is shutting down.

    For releasing process-wide resources a task acquired lazily — pooled HTTP
    clients to push gateways, say. Callbacks run in registration order and are
    isolated: one raising does not stop the others.
    """
    _shutdown_callbacks.append(fn)
    return fn


class WorkerShutdownMiddleware(dramatiq.Middleware):
    """Runs the ``on_worker_shutdown`` callbacks."""

    def after_worker_shutdown(self, broker, worker):
        for callback in _shutdown_callbacks:
            try:
                callback()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Worker shutdown callback %r failed", callback)


# ---------------------------------------------------------------------------
# Ownership tracking (gates the task-status endpoint)
# ---------------------------------------------------------------------------


def register_task_owner(task_id, user_id, *, actor_name=None, queue_name=None):
    """Record the owner of a task, plus what it takes to look its result up.

    Dramatiq reads a result by rebuilding the message that produced it, so the
    actor and queue names have to be remembered alongside the owner. They are
    optional only for the pre-minted-id path, where the caller knows the task
    but hasn't dispatched it yet; ``Task.track_owner()`` always fills them in.
    """
    cache.set(
        f"task_tracking:{task_id}",
        json.dumps(
            {
                "owner": str(user_id),
                "actor_name": actor_name,
                "queue_name": queue_name,
            }
        ),
        timeout=TASK_TRACKING_CACHE_TTL,
    )


def get_task_tracking(task_id: str) -> Optional[Dict[str, Any]]:
    """Tracking record for a task, or None when unknown/expired."""
    raw = cache.get(f"task_tracking:{task_id}")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Test / no-broker execution
# ---------------------------------------------------------------------------


class EagerBroker(StubBroker):
    """Run tasks inline on dispatch — the equivalent of Celery's eager mode.

    Used by the test suite and by the ``DevelopmentMinimal`` profile, which
    runs without Redis and without a worker. Exceptions propagate to the
    caller (Celery's eager mode swallowed them into the result, which quietly
    turned a broken task into a green test).

    Only ``CurrentMessage`` and ``Results`` run: the rest of the stack would be
    actively harmful inline — ``DbConnectionsMiddleware`` closes the connection
    the surrounding test is holding a transaction open on.
    """

    def enqueue(self, message, *, delay=None):
        # pylint: disable-next=import-outside-toplevel
        from dramatiq.results import Results

        actor = self.get_actor(message.actor_name)
        current = next(
            (m for m in self.middleware if isinstance(m, CurrentMessage)), None
        )
        results = next((m for m in self.middleware if isinstance(m, Results)), None)

        # Tasks enqueue other tasks; restore the caller's message on the way
        # out so its own set_task_progress() calls keep landing on its own id.
        previous = CurrentMessage.get_current_message() if current else None
        if current:
            current.before_process_message(self, message)
        try:
            result = actor.fn(*message.args, **message.kwargs)
            if results:
                results.after_process_message(self, message, result=result)
        finally:
            if current:
                current.after_process_message(self, message)
                if previous is not None:
                    current.before_process_message(self, previous)
        return message

"""Tests for ``core.task_utils`` — the background-task API.

Everything the rest of the codebase uses to declare and dispatch tasks goes
through here, so this covers the seam itself rather than any one task.
"""

# pylint: disable=redefined-outer-name

import uuid

from django.core.cache import cache

import pytest
from dramatiq.results import ResultMissing

from core import task_utils
from core.task_utils import (
    register_task,
    register_task_owner,
    set_task_progress,
)


class _Boom(Exception):
    """A transient failure worth retrying."""


class _Fatal(Exception):
    """A failure that is not worth retrying."""


@register_task(queue="default")
def _echo_task(value):
    """Return what it was given, so a caller can read a result back."""
    return {"echoed": value}


@register_task(queue="reindex", time_limit=90, max_retries=3, retry_on=(_Boom,))
def _picky_task():
    """Declared with an explicit retry policy, never actually dispatched."""


@register_task(queue="default")
def _progress_task():
    """Publish progress, and report the id it published under."""
    set_task_progress(42, {"message": "Halfway"})
    return {"task_id": task_utils.current_task_id()}


@register_task(queue="default")
def _report_progress_task(value):
    """Publish whatever it is handed, however nonsensical."""
    set_task_progress(value)


@register_task(queue="default")
def _explodes_task():
    """Always fails."""
    raise _Fatal("boom")


# Actor names are globally unique, so every test task lives at module level and
# is declared exactly once — declaring one inside a test body would blow up the
# second time that test ran (parametrised cases, reruns).


class TestRegisterTask:
    """The declaration side: queue, retries and time limits."""

    def test_queue_comes_from_the_decorator(self):
        assert _echo_task.queue_name == "default"
        assert _picky_task.queue_name == "reindex"

    def test_retries_are_off_by_default(self):
        """A failing task dead-letters instead of being re-run 20 times.

        This is the opposite of the queue library's own default, and matches
        what the Celery setup did — a task that wants retries asks for them.
        """
        assert _echo_task.options["max_retries"] == 0
        assert "retry_when" not in _echo_task.options

    def test_durations_are_declared_in_seconds(self):
        """The API takes seconds; the library wants milliseconds."""
        assert _picky_task.options["time_limit"] == 90_000
        assert _picky_task.options["max_backoff"] == 600_000

    def test_retry_on_restricts_which_failures_are_retried(self):
        retry_when = _picky_task.options["retry_when"]

        assert retry_when(0, _Boom("transient")) is True
        assert retry_when(2, _Boom("transient")) is True
        # Budget exhausted.
        assert retry_when(3, _Boom("transient")) is False
        # Not a listed exception: dead-letter at once, however much budget
        # is left.
        assert retry_when(0, _Fatal("bug")) is False

    def test_results_are_stored_by_default(self):
        """So a dispatched task can be polled through the status endpoint."""
        assert _echo_task.options["store_results"] is True

    def test_retry_on_without_a_budget_is_refused(self):
        """Otherwise the task advertises a retry policy and never retries.

        ``retry_on`` narrows *which* failures are retried; it does not grant
        any attempts. Declaring one without the other is always a mistake, and
        a silent one — the task dead-letters on its first failure.
        """
        with pytest.raises(ValueError, match="max_retries"):

            @register_task(queue="default", retry_on=(_Boom,))
            def _never_registered():
                pass


@pytest.mark.django_db
class TestDispatch:
    """The dispatch side: ``.delay()``, ``.apply_async()`` and tracking."""

    def test_delay_returns_a_task_with_an_id(self):
        task = _echo_task.delay("hello")

        assert uuid.UUID(task.id)
        assert task.actor_name == "_echo_task"
        assert task.queue_name == "default"

    def test_delay_runs_inline_under_the_eager_broker(self):
        """Tests and the no-Redis dev profile run tasks on dispatch."""
        task = _echo_task.delay("hello")

        assert task.get_result(block=False) == {"echoed": "hello"}

    def test_calling_the_task_runs_it_without_enqueuing(self):
        """Management commands and tests invoke tasks directly."""
        assert _echo_task("hello") == {"echoed": "hello"}

    def test_apply_async_honours_a_pre_minted_id(self):
        """Callers that must return an id before dispatching pass one in.

        ``POST /send/`` mints the id, hands it to the client, and only enqueues
        on transaction commit — so the id has to survive into the message.
        """
        task_id = str(uuid.uuid4())

        task = _echo_task.apply_async(args=["hi"], task_id=task_id)

        assert task.id == task_id
        assert task.get_result(block=False) == {"echoed": "hi"}

    def test_track_owner_records_what_the_status_endpoint_needs(self):
        """Results are keyed by (queue, actor, id), so all three are kept."""
        user_id = uuid.uuid4()
        task = _echo_task.delay("hello")

        task.track_owner(user_id)

        assert task_utils.get_task_tracking(task.id) == {
            "owner": str(user_id),
            "actor_name": "_echo_task",
            "queue_name": "default",
        }

    def test_tracking_is_none_for_an_unknown_task(self):
        assert task_utils.get_task_tracking("nope") is None

    def test_tracking_survives_a_corrupted_cache_entry(self):
        """A garbled record reads as "unknown", not as a 500."""
        cache.set("task_tracking:garbled", "{not json")

        assert task_utils.get_task_tracking("garbled") is None

    def test_register_task_owner_without_a_dispatch(self):
        """The pre-minted-id path has no message to read actor/queue from."""
        task_id = str(uuid.uuid4())

        register_task_owner(task_id, "42")

        assert task_utils.get_task_tracking(task_id) == {
            "owner": "42",
            "actor_name": None,
            "queue_name": None,
        }

    def test_a_failing_task_propagates_under_the_eager_broker(self):
        """Celery's eager mode swallowed exceptions into the result, which
        quietly turned a broken task into a green test. This one raises."""
        with pytest.raises(_Fatal):
            _explodes_task.delay()


@pytest.mark.django_db
class TestProgress:
    """Progress reporting, which replaces Celery's ``update_state``."""

    def test_progress_is_published_under_the_running_task_id(self):
        """A task reports against its own id, which is what the client polls."""
        task = _progress_task.delay()

        assert task.get_result(block=False)["task_id"] == task.id

        progress = task_utils.get_task_progress(task.id)
        assert progress["progress"] == 42
        assert progress["metadata"] == {"message": "Halfway"}
        assert progress["timestamp"] > 0

    def test_progress_outside_a_task_is_a_no_op(self):
        """The same code path runs synchronously from commands and tests."""
        set_task_progress(50, {"message": "nobody is listening"})

        assert task_utils.current_task_id() is None

    @pytest.mark.parametrize(
        "given,expected", [(-10, 0), (0, 0), (50, 50), (100, 100), (250, 100)]
    )
    def test_progress_is_clamped(self, given, expected):
        """A miscomputed percentage must not reach the client."""
        task = _report_progress_task.delay(given)

        assert task_utils.get_task_progress(task.id)["progress"] == expected

    def test_unparseable_progress_falls_back_to_zero(self):
        """Better a useless 0% than a 500 from the status endpoint."""
        task = _report_progress_task.delay("not a number")

        assert task_utils.get_task_progress(task.id)["progress"] == 0

    def test_a_task_returning_none_is_not_reported_as_pending(self):
        """A finished task must not look like one that never started.

        ``_report_progress_task`` returns None; if "no result recorded" and
        "recorded None" were conflated, the client would poll a completed task
        forever.
        """
        from django.contrib.auth import get_user_model

        from rest_framework.test import APIClient

        user = get_user_model().objects.create(
            email="none-result@example.com", password="x"
        )
        task = _report_progress_task.delay(10)
        task.track_owner(user.id)

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(f"/api/v1.0/tasks/{task.id}/")

        assert response.data["status"] == "SUCCESS"
        assert response.data["result"] is None

    def test_progress_is_absent_for_a_task_that_never_reported(self):
        task = _echo_task.delay("quiet")

        assert task_utils.get_task_progress(task.id) is None


class TestWorkerShutdown:
    """Callbacks that release process-wide resources when a worker stops."""

    def test_callbacks_run_and_one_failure_does_not_stop_the_rest(self):
        calls = []

        original = task_utils._shutdown_callbacks  # pylint: disable=protected-access
        task_utils._shutdown_callbacks = []  # pylint: disable=protected-access
        try:

            @task_utils.on_worker_shutdown
            def _explodes():
                calls.append("explodes")
                raise RuntimeError("cleanup failed")

            @task_utils.on_worker_shutdown
            def _also_runs():
                calls.append("also_runs")

            task_utils.WorkerShutdownMiddleware().after_worker_shutdown(None, None)
        finally:
            task_utils._shutdown_callbacks = original  # pylint: disable=protected-access

        assert calls == ["explodes", "also_runs"]


@pytest.mark.django_db
class TestResultLookup:
    """Reading a result back the way the status endpoint does."""

    def test_result_is_missing_for_an_unknown_id(self):
        """An id nobody ever dispatched has no result — not an empty one."""
        import dramatiq  # pylint: disable=import-outside-toplevel

        message = dramatiq.Message(
            queue_name="default",
            actor_name="_echo_task",
            args=(),
            kwargs={},
            options={},
            message_id=str(uuid.uuid4()),
        )

        with pytest.raises(ResultMissing):
            message.get_result(block=False)

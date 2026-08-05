"""How a held inbound message is paced and bounded.

Two halves of one policy: ``_RETRY_BACKOFF`` decides *when* the sweep asks
again, ``DEFERRAL_MAX_AGE`` decides when it stops asking. Selection is derived
from two existing columns — ``created_at`` for the age, ``updated_at`` for the
last attempt — so there is no per-row retry state to migrate. What these pin: a
row is not re-dispatched before its band's interval has passed, the interval
grows with age, a large backlog cannot be monopolised by its oldest rows, and
an expensive-but-transient failure is held for the full window rather than cut
short.
"""
# The task functions are bound Celery tasks; calling them directly is how the
# other task tests drive them.
# pylint: disable=unused-argument, no-value-for-parameter

from unittest.mock import patch

from django.utils import timezone

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from core import factories, models
from core.mda.inbound_pipeline import DEFERRAL_MAX_AGE
from core.mda.inbound_tasks import (
    _INBOUND_TASK_SOFT_TIME_LIMIT,
    _RETRY_BACKOFF,
    process_inbound_message_task,
    process_inbound_messages_queue_task,
)

TIMEDELTA_RIGHT_NOW = timezone.timedelta(0)


def _inbound(mailbox, age=TIMEDELTA_RIGHT_NOW, since_attempt=TIMEDELTA_RIGHT_NOW):
    """A queued row aged ``age`` whose last attempt was ``since_attempt`` ago."""
    blob = factories.BlobFactory(
        mailbox=mailbox, content=b"raw", content_type="message/rfc822"
    )
    inbound = models.InboundMessage.objects.create(mailbox=mailbox, blob=blob)
    now = timezone.now()
    models.InboundMessage.objects.filter(id=inbound.id).update(
        created_at=now - age, updated_at=now - since_attempt
    )
    inbound.refresh_from_db()
    return inbound


def _dispatched():
    """Run the sweep, returning the ids it re-dispatched."""
    with patch(
        "core.mda.inbound_tasks.process_inbound_message_task.delay"
    ) as delay_mock:
        process_inbound_messages_queue_task()
    return {call.args[0] for call in delay_mock.call_args_list}


@pytest.mark.django_db
class TestRetryBackoff:
    """Due-ness follows ``_RETRY_BACKOFF``."""

    def test_freshly_queued_row_is_left_alone(self):
        """The immediate ``.delay()`` dispatch owns the first few minutes."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox, age=TIMEDELTA_RIGHT_NOW, since_attempt=TIMEDELTA_RIGHT_NOW
        )
        assert str(inbound.id) not in _dispatched()

    def test_young_row_is_retried_on_the_short_interval(self):
        """Under 30 min old, a 5-minute-old attempt is due again."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox,
            age=timezone.timedelta(minutes=10),
            since_attempt=timezone.timedelta(minutes=6),
        )
        assert str(inbound.id) in _dispatched()

    def test_old_row_is_not_retried_on_the_short_interval(self):
        """The regression this whole change is about: a row that has been
        failing for hours must NOT be re-dispatched every 5 minutes."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox,
            age=timezone.timedelta(hours=6),
            since_attempt=timezone.timedelta(minutes=6),
        )
        assert str(inbound.id) not in _dispatched()

    def test_old_row_is_retried_once_its_longer_interval_elapses(self):
        """Backed off, not abandoned — it still gets attempts, just fewer."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox,
            age=timezone.timedelta(hours=6),
            since_attempt=timezone.timedelta(minutes=90),
        )
        assert str(inbound.id) in _dispatched()

    def test_intervals_grow_with_age(self):
        """Guards the table itself: a later band must wait longer than an
        earlier one, or the schedule isn't a backoff."""
        intervals = [interval for _, interval in _RETRY_BACKOFF]
        assert intervals == sorted(intervals)
        assert intervals[0] < intervals[-1]
        ages = [min_age for min_age, _ in _RETRY_BACKOFF]
        assert ages == sorted(ages)

    def test_a_running_row_is_not_re_dispatched(self):
        """``process_inbound_message_task`` stamps ``updated_at`` before doing
        any work, so a run that outlives a sweep interval doesn't have its
        batch slot burned by dispatches that only bounce off its lock."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox,
            age=timezone.timedelta(minutes=20),
            since_attempt=timezone.timedelta(minutes=6),
        )
        assert str(inbound.id) in _dispatched()

        # Simulate the attempt stamp taken at lock acquisition.
        models.InboundMessage.objects.filter(id=inbound.id).update(
            updated_at=timezone.now()
        )
        assert str(inbound.id) not in _dispatched()

    def test_oldest_rows_cannot_monopolise_the_batch(self):
        """With ``created_at`` ordering and a flat schedule, a backlog larger
        than the batch let the oldest rows hold every slot forever while newer
        ones aged out untried. Backing them off frees the slot: the old rows
        below are not due, so a single-slot batch goes to the young one."""
        mailbox = factories.MailboxFactory()
        old = [
            _inbound(
                mailbox,
                age=timezone.timedelta(hours=6),
                since_attempt=timezone.timedelta(minutes=6),
            )
            for _ in range(3)
        ]
        young = _inbound(
            mailbox,
            age=timezone.timedelta(minutes=20),
            since_attempt=timezone.timedelta(minutes=6),
        )

        dispatched = _dispatched()
        assert dispatched == {str(young.id)}
        assert not dispatched & {str(row.id) for row in old}

    def test_least_recently_attempted_goes_first(self):
        """Among rows that are all due, the one that has waited longest since
        its last attempt is dispatched first — so a run cut short by the batch
        cap starves nobody."""
        mailbox = factories.MailboxFactory()
        recent = _inbound(
            mailbox,
            age=timezone.timedelta(minutes=20),
            since_attempt=timezone.timedelta(minutes=6),
        )
        longest_waiting = _inbound(
            mailbox,
            age=timezone.timedelta(minutes=20),
            since_attempt=timezone.timedelta(minutes=25),
        )

        with patch(
            "core.mda.inbound_tasks.process_inbound_message_task.delay"
        ) as delay_mock:
            process_inbound_messages_queue_task()

        dispatched = [call.args[0] for call in delay_mock.call_args_list]
        assert dispatched == [str(longest_waiting.id), str(recent.id)]

    def test_a_backlog_larger_than_a_chunk_is_fully_dispatched(self):
        """The sweep streams the whole due set instead of dropping everything
        past the first slice until the next tick."""
        mailbox = factories.MailboxFactory()
        due = [
            _inbound(
                mailbox,
                age=timezone.timedelta(minutes=20),
                since_attempt=timezone.timedelta(minutes=6),
            )
            for _ in range(7)
        ]

        with patch(
            "core.mda.inbound_tasks.process_inbound_message_task.delay"
        ) as delay_mock:
            result = process_inbound_messages_queue_task(chunk_size=2)

        dispatched = [call.args[0] for call in delay_mock.call_args_list]
        assert set(dispatched) == {str(row.id) for row in due}
        # Each row dispatched exactly once, not once per chunk round trip.
        assert len(dispatched) == len(due)
        assert result["total"] == len(due)

    def test_the_dispatch_cap_bounds_one_run(self):
        """A run cannot enqueue an unbounded burst."""
        mailbox = factories.MailboxFactory()
        for _ in range(7):
            _inbound(
                mailbox,
                age=timezone.timedelta(minutes=20),
                since_attempt=timezone.timedelta(minutes=6),
            )

        with patch(
            "core.mda.inbound_tasks.process_inbound_message_task.delay"
        ) as delay_mock:
            result = process_inbound_messages_queue_task(chunk_size=2, max_dispatch=4)

        assert len(delay_mock.call_args_list) == 4
        assert result["total"] == 4

    def test_an_exactly_full_run_is_not_reported_as_capped(self):
        """The cap is detected by fetching one row past the limit, so a run
        landing exactly on ``max_dispatch`` must not warn."""
        mailbox = factories.MailboxFactory()
        for _ in range(4):
            _inbound(
                mailbox,
                age=timezone.timedelta(minutes=20),
                since_attempt=timezone.timedelta(minutes=6),
            )

        with (
            patch("core.mda.inbound_tasks.process_inbound_message_task.delay"),
            patch("core.mda.inbound_tasks.logger.warning") as warn_mock,
        ):
            result = process_inbound_messages_queue_task(max_dispatch=4)

        assert result["total"] == 4
        assert not warn_mock.called

    def test_rows_sharing_a_timestamp_are_not_skipped(self):
        """Ordering by a non-unique column must not drop rows that tie on it."""
        mailbox = factories.MailboxFactory()
        rows = [
            _inbound(
                mailbox,
                age=timezone.timedelta(minutes=20),
                since_attempt=timezone.timedelta(minutes=6),
            )
            for _ in range(5)
        ]
        # Every row attempted at the exact same instant.
        same_instant = timezone.now() - timezone.timedelta(minutes=6)
        models.InboundMessage.objects.filter(id__in=[row.id for row in rows]).update(
            updated_at=same_instant
        )

        with patch(
            "core.mda.inbound_tasks.process_inbound_message_task.delay"
        ) as delay_mock:
            process_inbound_messages_queue_task(chunk_size=2)

        dispatched = [call.args[0] for call in delay_mock.call_args_list]
        assert set(dispatched) == {str(row.id) for row in rows}
        assert len(dispatched) == len(rows)

    def test_only_ids_are_fetched(self):
        """Nothing here should be proportional to message size: the sweep must
        not materialise model instances (or touch blobs) to dispatch ids."""
        mailbox = factories.MailboxFactory()
        _inbound(
            mailbox,
            age=timezone.timedelta(minutes=20),
            since_attempt=timezone.timedelta(minutes=6),
        )

        with (
            patch("core.mda.inbound_tasks.process_inbound_message_task.delay"),
            patch.object(
                models.InboundMessage, "get_raw_bytes", side_effect=AssertionError
            ),
        ):
            result = process_inbound_messages_queue_task()

        assert result["processed"] == 1

    def test_abandoned_rows_stay_excluded(self):
        """Unchanged by the rewrite: a poison row must never be re-dispatched."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox,
            age=timezone.timedelta(hours=6),
            since_attempt=timezone.timedelta(hours=6),
        )
        models.InboundMessage.objects.filter(id=inbound.id).update(
            abandoned_at=timezone.now()
        )
        assert str(inbound.id) not in _dispatched()


@pytest.mark.django_db
class TestSoftTimeLimitIsCaughtAndBounded:
    """``SoftTimeLimitExceeded`` has its own ``except`` branch, ahead of the
    generic one: it must bail out gracefully — release the lock, hold the row
    — rather than propagate and let the hard limit kill the task mid-flight.
    The window it uses is the generic one; a slow dependency is transient, and
    the backoff schedule rather than a shorter deadline is what keeps those
    retries affordable."""

    def _run_with_timeout(self, inbound):
        with patch(
            "core.mda.inbound_tasks.parse_email",
            side_effect=SoftTimeLimitExceeded(),
        ):
            return process_inbound_message_task(str(inbound.id))

    def test_recent_message_is_still_held_for_retry(self):
        """The timeout is caught, not propagated, and the row is held: the
        transient case (a slow webhook chain recovering) gets its retries."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(mailbox)

        result = self._run_with_timeout(inbound)

        assert result["error"] == "retry"
        inbound.refresh_from_db()
        assert inbound.abandoned_at is None

    def test_message_held_past_an_hour_is_not_abandoned(self):
        """Regression guard on a deliberate removal: this path once had its
        own 1h window, so an hour of provider slowness lost the message."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(mailbox, age=timezone.timedelta(hours=6))

        result = self._run_with_timeout(inbound)

        assert result["error"] == "retry"
        inbound.refresh_from_db()
        assert inbound.abandoned_at is None

    def test_message_past_the_deferral_window_is_abandoned(self):
        """Still bounded, and the reason names the limit that was hit, which
        is what tells an operator this was a timeout and not a crash."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(
            mailbox, age=DEFERRAL_MAX_AGE + timezone.timedelta(minutes=5)
        )

        result = self._run_with_timeout(inbound)

        assert result["error"] == "abandoned"
        inbound.refresh_from_db()
        assert inbound.abandoned_at is not None
        assert str(_INBOUND_TASK_SOFT_TIME_LIMIT) in inbound.error_message

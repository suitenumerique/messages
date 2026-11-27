"""Property tests: iTIP REPLYs give the same answers in any processing order."""
# pylint: disable=protected-access, missing-function-docstring

from datetime import datetime, timezone
from itertools import permutations, product

from django.core.cache import cache

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from icalendar import Calendar as ICalendar

from core.services.calendar.service import CalDAVTransientError
from core.tests.services.test_calendar_reply import (
    ATTENDEE,
    MOVED,
    REGULAR,
    SERIES,
    _partstat,
    _reply,
)
from core.tests.services.test_calendar_reply_order import (
    DECLINED_SERIES,
    _apply_to_resource,
)

ACCEPTED_SERIES = SERIES.replace(
    f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{ATTENDEE}",
    f"PARTSTAT=ACCEPTED:mailto:{ATTENDEE}",
)

# The dates of MOVED and REGULAR, and a date that no reply names.
DATES = (
    datetime(2026, 3, 9, 9, tzinfo=timezone.utc),
    datetime(2026, 3, 16, 9, tzinfo=timezone.utc),
    datetime(2026, 3, 23, 9, tzinfo=timezone.utc),
)

# What happens to the write of one processing of a reply.
OK, FAIL, REACH = "ok", "fail", "reach"

STARTS = (SERIES, DECLINED_SERIES, ACCEPTED_SERIES)
ANSWERS = tuple(product((None, MOVED, REGULAR), ("ACCEPTED", "DECLINED")))

answers = st.lists(
    st.sampled_from(ANSWERS),
    min_size=1,
    max_size=2,
    unique_by=lambda answer: answer[0],
)


@st.composite
def scenarios(draw):
    """Draw the replies in arrival order, and the order of their processing.

    Each reply is processed until a write succeeds. Before that, a write can
    fail, or reach the server and still fail. A relay can also deliver a
    reply twice.
    """
    replies = draw(st.lists(answers, min_size=1, max_size=3))
    faults = [
        [*draw(st.lists(st.sampled_from([OK, FAIL, REACH]), max_size=2)), OK]
        for _ in replies
    ]
    tokens = [index for index, attempts in enumerate(faults) for _ in attempts]
    order = draw(st.permutations(tokens))
    seen = [0] * len(replies)
    steps = []
    for index in order:
        steps.append((index, faults[index][seen[index]]))
        seen[index] += 1
    # The last processing of each reply has a write that succeeds.
    last = {index: step for step, (index, _) in enumerate(steps)}
    steps = [
        (index, OK if last[index] == step else fault)
        for step, (index, fault) in enumerate(steps)
    ]
    return replies, steps


def _received_at(index):
    return 100.0 * (index + 1)


def _answers(ics):
    """Return the answer of the attendee for the series and on each date."""
    events = ICalendar.from_ical(ics).walk("VEVENT")
    series = next(event for event in events if "RECURRENCE-ID" not in event)

    def on(date):
        for event in events:
            recurrence_id = event.get("RECURRENCE-ID")
            if recurrence_id and recurrence_id.dt.astimezone(timezone.utc) == date:
                return _partstat(event)
        return _partstat(series)

    return (_partstat(series), *(on(date) for date in DATES))


def _run(start, replies, steps):
    """Process the replies on a server that holds ``start``. Return its event."""
    cache.clear()
    server = [start]

    for index, fault in steps:

        def put(_url, body, _etag=None, fault=fault):
            if fault != FAIL:
                server[0] = body
            if fault != OK:
                raise CalDAVTransientError("unavailable", status_code=503)

        try:
            _apply_to_resource(
                _reply(*((rid, partstat, 0) for rid, partstat in replies[index])),
                ICalendar.from_ical(server[0]),
                received_at=_received_at(index),
                put=put,
            )
        except CalDAVTransientError:
            pass
    return server[0]


def _differs(start, replies, steps):
    in_order = [(index, OK) for index in range(len(replies))]
    expected = _answers(_run(start, replies, in_order))
    return _answers(_run(start, replies, steps)) != expected


@pytest.mark.parametrize("start", STARTS, ids=["needs-action", "declined", "accepted"])
def test_every_processing_order_gives_the_answers_of_the_arrival_order(start):
    failures = []
    for count in (2, 3):
        for combination in product(ANSWERS, repeat=count):
            replies = [[answer] for answer in combination]
            for order in permutations(range(count)):
                steps = [(index, OK) for index in order]
                if _differs(start, replies, steps):
                    failures.append((replies, order))
    cache.clear()
    assert not failures, failures[:5]


@settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(start=st.sampled_from(STARTS), scenario=scenarios())
def test_failed_and_repeated_writes_give_the_answers_of_the_arrival_order(
    start, scenario
):
    replies, steps = scenario
    differs = _differs(start, replies, steps)
    cache.clear()
    assert not differs, steps

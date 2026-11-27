"""Tests for the order in which iTIP REPLYs change a recurring event."""
# pylint: disable=protected-access, missing-function-docstring

from dataclasses import replace
from unittest import mock

from django.core.cache import cache

import pytest
from django_redis.exceptions import ConnectionInterrupted
from icalendar import Calendar as ICalendar

from core.services.calendar.reply import parse_reply
from core.services.calendar.reply_order import reply_order_key
from core.services.calendar.service import CalDAVService, CalDAVTransientError
from core.tests.services.test_calendar_reply import (
    ATTENDEE,
    MOVED,
    OTHER,
    REGULAR,
    RESOURCE,
    SERIES,
    _partstat,
    _reply,
)


def _apply_to_resource(ics, stored, received_at=None, put=None):
    """Apply a REPLY to a stored resource. Return the result and the PUT mock."""
    service = CalDAVService(url="http://caldav.example/")
    reply, _reason = parse_reply(ics, ATTENDEE)
    reply = replace(reply, received_at=received_at)
    with mock.patch.object(service, "_put_resource", side_effect=put) as put_mock:
        result = service._apply_reply_to_resource(RESOURCE, stored, '"1"', reply)
    return result, put_mock


@pytest.fixture(name="reply_order")
def fixture_reply_order():
    """Start and end with no recorded reply."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.usefixtures("reply_order")
def test_old_reply_does_not_change_a_newer_answer():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((None, "DECLINED", 0)), stored, received_at=200.0)

    result, put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )

    assert result == {"applied": False, "reason": "superseded"}
    assert put.call_count == 0
    assert _partstat(stored.walk("VEVENT")[0]) == "DECLINED"


@pytest.mark.usefixtures("reply_order")
def test_old_reply_does_not_change_an_answer_that_was_already_there():
    stored = ICalendar.from_ical(SERIES.replace("NEEDS-ACTION", "ACCEPTED"))
    result, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=200.0
    )
    assert result == {"applied": False, "reason": "unchanged"}

    result, _put = _apply_to_resource(
        _reply((None, "DECLINED", 0)), stored, received_at=100.0
    )

    assert result == {"applied": False, "reason": "superseded"}
    assert _partstat(stored.walk("VEVENT")[0]) == "ACCEPTED"


# The attendee declined all the occurrences.
DECLINED_SERIES = SERIES.replace(
    f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{ATTENDEE}",
    f"PARTSTAT=DECLINED:mailto:{ATTENDEE}",
    1,
)


@pytest.mark.usefixtures("reply_order")
def test_old_answer_for_the_series_comes_before_a_newer_one():
    # In order: DECLINED for the series leaves the declined exception. Then
    # ACCEPTED for the series goes to it, because it has the old answer.
    stored = ICalendar.from_ical(
        SERIES.replace(
            "SEQUENCE:1\nSUMMARY:Weekly\nORGANIZER:mailto:organizer@example.com\n"
            "ATTENDEE;PARTSTAT=NEEDS-ACTION",
            "SEQUENCE:1\nSUMMARY:Weekly\nORGANIZER:mailto:organizer@example.com\n"
            "ATTENDEE;PARTSTAT=DECLINED",
        )
    )
    _apply_to_resource(_reply((None, "ACCEPTED", 0)), stored, received_at=200.0)
    assert _partstat(stored.walk("VEVENT")[1]) == "DECLINED"

    result, _put = _apply_to_resource(
        _reply((None, "DECLINED", 0)), stored, received_at=100.0
    )

    assert result == {"applied": True, "reason": "applied"}
    assert [_partstat(event) for event in stored.walk("VEVENT")] == [
        "ACCEPTED",
        "ACCEPTED",
    ]


@pytest.mark.usefixtures("reply_order")
def test_old_reply_keeps_changes_to_an_exception_of_a_newer_reply():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((REGULAR, "DECLINED", 0)), stored, received_at=200.0)
    added = stored.walk("VEVENT")[2]
    # Another attendee accepts this date, and the organizer moves it to 14:00.
    added["ATTENDEE"][1].params["PARTSTAT"] = "ACCEPTED"
    added["DTSTART"].dt = added["DTSTART"].dt.replace(hour=14)

    result, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )

    series, moved, added = stored.walk("VEVENT")
    assert result == {"applied": True, "reason": "applied"}
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(moved) == "ACCEPTED"
    assert _partstat(added) == "DECLINED"
    assert _partstat(added, OTHER) == "ACCEPTED"
    assert added["DTSTART"].to_ical() == b"20260316T140000"


@pytest.mark.usefixtures("reply_order")
def test_state_keeps_the_newest_replies():
    stored = ICalendar.from_ical(SERIES)
    with mock.patch("core.services.calendar.reply_order.MAX_REPLY_ORDER_ANSWERS", 2):
        for at, partstat in (
            (100.0, "ACCEPTED"),
            (200.0, "DECLINED"),
            (300.0, "TENTATIVE"),
        ):
            _apply_to_resource(_reply((None, partstat, 0)), stored, received_at=at)
        # Older than a reply that went into the answers from before.
        result, _put = _apply_to_resource(
            _reply((None, "DECLINED", 0)), stored, received_at=50.0
        )

    state = cache.get(reply_order_key(RESOURCE, ATTENDEE))
    assert [old["at"] for old in state["replies"]] == [200.0, 300.0]
    assert state["floor"] == 100.0
    assert result == {"applied": False, "reason": "superseded"}
    assert _partstat(stored.walk("VEVENT")[0]) == "TENTATIVE"


@pytest.mark.usefixtures("reply_order")
def test_state_limits_the_answers_of_its_replies():
    stored = ICalendar.from_ical(SERIES)
    with mock.patch("core.services.calendar.reply_order.MAX_REPLY_ORDER_ANSWERS", 3):
        _apply_to_resource(
            _reply((MOVED, "ACCEPTED", 0), (REGULAR, "DECLINED", 0)),
            stored,
            received_at=100.0,
        )
        _apply_to_resource(
            _reply((None, "TENTATIVE", 0), (MOVED, "DECLINED", 0)),
            stored,
            received_at=200.0,
        )

    state = cache.get(reply_order_key(RESOURCE, ATTENDEE))
    assert [old["at"] for old in state["replies"]] == [200.0]
    assert state["floor"] == 100.0
    assert [_partstat(event) for event in stored.walk("VEVENT")] == [
        "TENTATIVE",
        "DECLINED",
        "DECLINED",
    ]


@pytest.mark.usefixtures("reply_order")
def test_old_reply_keeps_a_later_change_on_another_date():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((REGULAR, "DECLINED", 0)), stored, received_at=200.0)
    # Then the organizer changes the attendee on 9 March.
    moved = stored.walk("VEVENT")[1]
    moved["ATTENDEE"][0].params["PARTSTAT"] = "ACCEPTED"
    moved["ATTENDEE"][0].params["ROLE"] = "OPT-PARTICIPANT"

    _apply_to_resource(_reply((REGULAR, "TENTATIVE", 0)), stored, received_at=100.0)

    _, moved, added = stored.walk("VEVENT")
    assert _partstat(moved) == "ACCEPTED"
    assert moved["ATTENDEE"][0].params["ROLE"] == "OPT-PARTICIPANT"
    assert _partstat(added) == "DECLINED"


@pytest.mark.usefixtures("reply_order")
def test_change_after_the_replies_wins_over_an_older_reply():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((REGULAR, "DECLINED", 0)), stored, received_at=200.0)
    # Then the organizer changes the answer of the attendee on 16 March.
    stored.walk("VEVENT")[2]["ATTENDEE"][0].params["PARTSTAT"] = "TENTATIVE"

    old, _put = _apply_to_resource(
        _reply((MOVED, "ACCEPTED", 0)), stored, received_at=100.0
    )
    new, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=300.0
    )

    series, moved, added = stored.walk("VEVENT")
    assert old == {"applied": False, "reason": "superseded"}
    assert new == {"applied": True, "reason": "applied"}
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(moved) == "ACCEPTED"
    assert _partstat(added) == "TENTATIVE"


@pytest.mark.usefixtures("reply_order")
def test_reply_keeps_a_reconfirmation_request_on_another_date():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((None, "ACCEPTED", 0)), stored, received_at=100.0)
    # The organizer moves 9 March again, and asks the attendee to confirm.
    attendee = stored.walk("VEVENT")[1]["ATTENDEE"][0]
    attendee.params["RSVP"] = "TRUE"
    del attendee.params["SCHEDULE-STATUS"]

    _apply_to_resource(_reply((REGULAR, "DECLINED", 0)), stored, received_at=200.0)

    _series, moved, added = stored.walk("VEVENT")
    assert dict(moved["ATTENDEE"][0].params) == {
        "PARTSTAT": "ACCEPTED",
        "RSVP": "TRUE",
    }
    assert _partstat(added) == "DECLINED"


@pytest.mark.usefixtures("reply_order")
def test_cache_error_before_the_write_keeps_the_order():
    server = [SERIES]

    def apply(ics, received_at):
        def put(_url, body, _etag=None):
            server[0] = body

        stored = ICalendar.from_ical(server[0])
        return _apply_to_resource(ics, stored, received_at=received_at, put=put)

    apply(_reply((None, "DECLINED", 0)), 50.0)
    with (
        mock.patch.object(
            cache, "set", side_effect=ConnectionInterrupted(connection=None)
        ),
        pytest.raises(ConnectionInterrupted),
    ):
        apply(_reply((None, "ACCEPTED", 0)), 200.0)
    # The task retries the reply, then an older reply for one date arrives.
    apply(_reply((None, "ACCEPTED", 0)), 200.0)
    apply(_reply((REGULAR, "DECLINED", 0)), 100.0)

    # In order, the date is DECLINED like the series and needs no exception.
    # Then it follows the series.
    series, _moved = ICalendar.from_ical(server[0]).walk("VEVENT")
    assert _partstat(series) == "ACCEPTED"


@pytest.mark.usefixtures("reply_order")
def test_reply_of_a_failed_write_stays_after_a_change_by_the_organizer():
    server = [SERIES]

    def apply(ics, received_at, put=None):
        def store(_url, body, _etag=None):
            server[0] = body

        stored = ICalendar.from_ical(server[0])
        return _apply_to_resource(
            ics, stored, received_at=received_at, put=put or store
        )

    failure = CalDAVTransientError("unavailable", status_code=503)
    with pytest.raises(CalDAVTransientError):
        apply(_reply((None, "ACCEPTED", 0)), 200.0, put=mock.Mock(side_effect=failure))
    # The organizer changes the answer of the attendee before the retry.
    stored = ICalendar.from_ical(server[0])
    stored.walk("VEVENT")[0]["ATTENDEE"][0].params["PARTSTAT"] = "DECLINED"
    server[0] = stored.to_ical().decode()
    apply(_reply((None, "ACCEPTED", 0)), 200.0)
    apply(_reply((REGULAR, "DECLINED", 0)), 100.0)

    # In order, the date is DECLINED like the series and needs no exception.
    # Then it follows the series.
    series, _moved = ICalendar.from_ical(server[0]).walk("VEVENT")
    assert _partstat(series) == "ACCEPTED"


@pytest.mark.usefixtures("reply_order")
def test_state_covers_the_inbound_deferral(settings):
    stored = ICalendar.from_ical(SERIES)
    deferral = settings.MESSAGES_INBOUND_DEFERRAL_MAX_AGE
    with mock.patch.object(cache, "set", wraps=cache.set) as cache_set:
        _apply_to_resource(_reply((None, "DECLINED", 0)), stored, received_at=100.0)
        _apply_to_resource(
            _reply((None, "ACCEPTED", 0)), stored, received_at=100.0 + deferral
        )

    state = cache.get(reply_order_key(RESOURCE, ATTENDEE))
    assert [old["at"] for old in state["replies"]] == [100.0, 100.0 + deferral]
    assert cache_set.call_args.args[2] > deferral


@pytest.mark.usefixtures("reply_order")
def test_arrival_times_of_an_older_version_are_ignored():
    cache.set(reply_order_key(RESOURCE, ATTENDEE), {None: 200.0}, 60)
    stored = ICalendar.from_ical(SERIES)

    result, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )

    assert result == {"applied": True, "reason": "applied"}


@pytest.mark.usefixtures("reply_order")
def test_same_reply_twice_is_kept_once():
    stored = ICalendar.from_ical(SERIES)
    first, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )
    again, put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )

    assert first == {"applied": True, "reason": "applied"}
    assert again == {"applied": False, "reason": "unchanged"}
    assert put.call_count == 0
    state = cache.get(reply_order_key(RESOURCE, ATTENDEE))
    assert state["replies"] == (
        {
            "at": 100.0,
            "answers": ({"recurrence_id": None, "partstat": "ACCEPTED"},),
            "written": True,
        },
    )


@pytest.mark.usefixtures("reply_order")
def test_old_reply_for_the_series_does_not_go_to_an_exception_with_a_newer_answer():
    # The attendee answered TENTATIVE for all the occurrences.
    stored = ICalendar.from_ical(
        SERIES.replace(
            f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{ATTENDEE}",
            f"PARTSTAT=TENTATIVE:mailto:{ATTENDEE}",
        )
    )
    # The newer reply gives the same answer again for the moved occurrence.
    _apply_to_resource(_reply((MOVED, "TENTATIVE", 1)), stored, received_at=200.0)

    result, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )

    series, exception = stored.walk("VEVENT")
    assert result == {"applied": True, "reason": "applied"}
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "TENTATIVE"


@pytest.mark.usefixtures("reply_order")
def test_reply_without_arrival_time_is_applied_in_the_order_of_the_tasks():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((None, "DECLINED", 0)), stored, received_at=200.0)

    result, _put = _apply_to_resource(_reply((None, "ACCEPTED", 0)), stored)

    assert result == {"applied": True, "reason": "applied"}

"""Tests for the occurrences of a recurring VEVENT."""
# pylint: disable=missing-function-docstring

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar as ICalendar

from core.services.calendar.recurrence import occurrence_override, recurrence_key

PARIS = ZoneInfo("Europe/Paris")

WEEKLY = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:series@example.com
DTSTAMP:20260101T000000Z
DTSTART;TZID=Europe/Paris:20260302T100000
DTEND;TZID=Europe/Paris:20260302T110000
RRULE:FREQ=WEEKLY;UNTIL=20260601T080000Z
EXDATE;TZID=Europe/Paris:20260316T100000
RDATE;TZID=Europe/Paris:20260305T100000
SEQUENCE:2
SUMMARY:Weekly
ORGANIZER:mailto:organizer@example.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:attendee@example.com
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
END:VALARM
END:VEVENT
END:VCALENDAR"""


def _master(ics=WEEKLY):
    return ICalendar.from_ical(ics).walk("VEVENT")[0]


def test_key_is_the_same_for_one_instant_in_two_timezones():
    assert recurrence_key(datetime(2026, 3, 30, 10, 0, tzinfo=PARIS)) == (
        recurrence_key(datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc))
    )


def test_key_differs_between_kinds_of_values():
    keys = {
        recurrence_key(date(2026, 3, 30)),
        recurrence_key(datetime(2026, 3, 30)),
        recurrence_key(datetime(2026, 3, 30, tzinfo=timezone.utc)),
    }
    assert len(keys) == 3


def test_override_is_the_occurrence_without_the_rule():
    # After the change to summer time, 10:00 in Paris is 08:00 UTC.
    override = occurrence_override(
        _master(), datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)
    )

    for name, wall_time in (
        ("RECURRENCE-ID", b"20260330T100000"),
        ("DTSTART", b"20260330T100000"),
        ("DTEND", b"20260330T110000"),
    ):
        assert override[name].to_ical() == wall_time
        assert override[name].params["TZID"] == "Europe/Paris"
    for name in ("RRULE", "EXDATE", "RDATE"):
        assert name not in override
    assert override["SEQUENCE"] == 2
    assert override["ATTENDEE"].params["PARTSTAT"] == "ACCEPTED"
    assert len(override.walk("VALARM")) == 1


def test_override_does_not_change_the_master():
    master = _master()
    occurrence_override(master, datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc))

    assert "RRULE" in master
    assert master["DTSTART"].to_ical() == b"20260302T100000"


@pytest.mark.parametrize(
    "recurrence_id",
    [
        pytest.param(datetime(2026, 3, 16, 10, 0, tzinfo=PARIS), id="excluded date"),
        pytest.param(datetime(2026, 3, 30, 11, 0, tzinfo=PARIS), id="other time"),
        pytest.param(datetime(2026, 6, 8, 10, 0, tzinfo=PARIS), id="after the end"),
        pytest.param(datetime(2026, 2, 23, 10, 0, tzinfo=PARIS), id="before the start"),
        pytest.param(datetime(2026, 3, 30, 10, 0), id="without timezone"),
        pytest.param(date(2026, 3, 30), id="date"),
    ],
)
def test_no_override_when_the_series_has_no_such_occurrence(recurrence_id):
    assert occurrence_override(_master(), recurrence_id) is None


def test_no_override_for_an_event_that_does_not_repeat():
    ics = "\n".join(
        line
        for line in WEEKLY.splitlines()
        if not line.startswith(("RRULE", "EXDATE", "RDATE"))
    )

    assert (
        occurrence_override(_master(ics), datetime(2026, 3, 2, 10, 0, tzinfo=PARIS))
        is None
    )


def test_no_override_when_the_rule_cannot_be_expanded():
    # A local DTSTART with an UNTIL in UTC is not valid.
    ics = WEEKLY.replace(";TZID=Europe/Paris", "")

    assert occurrence_override(_master(ics), datetime(2026, 3, 9, 10, 0)) is None


def test_override_for_an_all_day_series():
    ics = (
        WEEKLY.replace(
            "DTSTART;TZID=Europe/Paris:20260302T100000", "DTSTART;VALUE=DATE:20260302"
        )
        .replace("DTEND;TZID=Europe/Paris:20260302T110000", "DTEND;VALUE=DATE:20260303")
        .replace(
            "RRULE:FREQ=WEEKLY;UNTIL=20260601T080000Z", "RRULE:FREQ=WEEKLY;COUNT=4"
        )
        .replace("EXDATE;TZID=Europe/Paris:20260316T100000\n", "")
        .replace("RDATE;TZID=Europe/Paris:20260305T100000\n", "")
    )

    override = occurrence_override(_master(ics), date(2026, 3, 9))

    for name, value in (
        ("RECURRENCE-ID", b"20260309"),
        ("DTSTART", b"20260309"),
        ("DTEND", b"20260310"),
    ):
        assert override[name].to_ical() == value
        assert override[name].params["VALUE"] == "DATE"
    assert occurrence_override(_master(ics), date(2026, 3, 30)) is None


def test_override_for_an_added_date_in_another_timezone():
    # 10:00 in New York is 16:00 in Paris on 5 March.
    ics = WEEKLY.replace(
        "RDATE;TZID=Europe/Paris:20260305T100000",
        "RDATE;TZID=America/New_York:20260305T100000",
    )

    override = occurrence_override(
        _master(ics), datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc)
    )

    for name, wall_time in (
        ("RECURRENCE-ID", b"20260305T160000"),
        ("DTSTART", b"20260305T160000"),
        ("DTEND", b"20260305T170000"),
    ):
        assert override[name].to_ical() == wall_time
        assert override[name].params["TZID"] == "Europe/Paris"


def test_override_keeps_the_exact_duration_across_a_time_change():
    # Summer time starts on 29 March at 02:00. Three hours from 01:00 end at 05:00.
    ics = WEEKLY.replace(
        "DTSTART;TZID=Europe/Paris:20260302T100000",
        "DTSTART;TZID=Europe/Paris:20260322T010000",
    ).replace(
        "DTEND;TZID=Europe/Paris:20260302T110000",
        "DTEND;TZID=Europe/Paris:20260322T040000",
    )

    override = occurrence_override(
        _master(ics), datetime(2026, 3, 29, 1, 0, tzinfo=PARIS)
    )

    assert override["DTSTART"].to_ical() == b"20260329T010000"
    assert override["DTEND"].to_ical() == b"20260329T050000"
    assert override["DTEND"].params["TZID"] == "Europe/Paris"

"""Tests for the parse of the ICS of an email without its time zones."""
# pylint: disable=missing-function-docstring

from datetime import timedelta

import pytest
from icalendar import Calendar as ICalendar
from icalendar.timezone import tzp

from core.services.calendar.untrusted_ics import parse_untrusted_ics

# A wrong definition of a Windows name. Outlook uses these names.
POISON = (
    "BEGIN:VTIMEZONE\r\nTZID:Romance Standard Time\r\n"
    "BEGIN:STANDARD\r\nDTSTART:16010101T000000\r\n"
    "TZOFFSETFROM:-2300\r\nTZOFFSETTO:-2300\r\nEND:STANDARD\r\n"
    "END:VTIMEZONE\r\n"
)


def _reply(vtimezone=""):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        "METHOD:REPLY\r\n" + vtimezone + "BEGIN:VEVENT\r\nUID:u@example.com\r\n"
        "RECURRENCE-ID;TZID=Romance Standard Time:20260106T100000\r\n"
        "ATTENDEE;PARTSTAT=DECLINED:mailto:attendee@example.com\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def _offset(cal):
    (event,) = cal.walk("VEVENT")
    return event["RECURRENCE-ID"].dt.utcoffset()


def test_time_zone_of_another_parse_is_not_used():
    tzp.use_default()
    assert _offset(ICalendar.from_ical(_reply(POISON))) == timedelta(hours=-23)

    assert _offset(parse_untrusted_ics(_reply())) == timedelta(hours=1)


def test_time_zone_of_the_email_is_not_used_or_kept():
    # A folded line still starts a VTIMEZONE for the parser.
    folded = POISON.replace("BEGIN:VTIMEZONE", "BEGIN:VTIME\r\n ZONE")

    assert _offset(parse_untrusted_ics(_reply(folded))) == timedelta(hours=1)
    assert _offset(ICalendar.from_ical(_reply())) == timedelta(hours=1)


def test_end_of_a_time_zone_without_its_begin_is_not_kept():
    # The parser keeps a time zone at END:VTIMEZONE, whatever the BEGIN is.
    stray = POISON.replace("BEGIN:VTIMEZONE", "BEGIN:X-ZONE")

    with pytest.raises(ValueError):
        parse_untrusted_ics(_reply(stray))

    assert _offset(ICalendar.from_ical(_reply())) == timedelta(hours=1)

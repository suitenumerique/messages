"""Tests for how an iTIP REPLY changes the VEVENTs of a recurring event."""
# pylint: disable=protected-access, missing-function-docstring

import pytest
from icalendar import Calendar as ICalendar

from core.services.calendar.service import CalDAVService

ATTENDEE = "attendee@example.com"
OTHER = "other@example.com"

# A weekly series. The organizer moved the occurrence of 9 March to 14:00.
SERIES = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:series@example.com
DTSTAMP:20260101T000000Z
DTSTART;TZID=Europe/Paris:20260302T100000
DTEND;TZID=Europe/Paris:20260302T110000
RRULE:FREQ=WEEKLY;COUNT=10
SEQUENCE:0
SUMMARY:Weekly
ORGANIZER:mailto:organizer@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:attendee@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:other@example.com
END:VEVENT
BEGIN:VEVENT
UID:series@example.com
DTSTAMP:20260101T000000Z
RECURRENCE-ID;TZID=Europe/Paris:20260309T100000
DTSTART;TZID=Europe/Paris:20260309T140000
DTEND;TZID=Europe/Paris:20260309T150000
SEQUENCE:1
SUMMARY:Weekly
ORGANIZER:mailto:organizer@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:attendee@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:other@example.com
END:VEVENT
END:VCALENDAR"""

# The moved occurrence, written as an instant in UTC.
MOVED = "RECURRENCE-ID:20260309T090000Z"
# An occurrence that has no exception in the stored event.
REGULAR = "RECURRENCE-ID:20260316T090000Z"


def _reply(*answers, uid="series@example.com"):
    """Build a REPLY with one VEVENT for each ``(recurrence_id, partstat, sequence)``.

    ``recurrence_id`` is a RECURRENCE-ID line, or None for the series.
    """
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN", "METHOD:REPLY"]
    for recurrence_id, partstat, sequence in answers:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            "DTSTAMP:20260102T000000Z",
            "DTSTART:20260302T090000Z",
            f"SEQUENCE:{sequence}",
            "ORGANIZER:mailto:organizer@example.com",
            f"ATTENDEE;PARTSTAT={partstat}:mailto:{ATTENDEE}",
            *([recurrence_id] if recurrence_id else []),
            "END:VEVENT",
        ]
    return "\n".join([*lines, "END:VCALENDAR"])


def _apply(ics, stored=None):
    """Apply a REPLY to ``stored``, by default a new copy of the series.

    Return the outcomes and the stored VEVENTs.
    """
    if stored is None:
        stored = ICalendar.from_ical(SERIES)
    reply, reason = CalDAVService._parse_reply(ics, ATTENDEE)
    assert reason is None
    outcomes = CalDAVService(url="http://caldav.example/")._apply_answers(stored, reply)
    return outcomes, stored.walk("VEVENT")


def _partstat(event, email=ATTENDEE):
    return next(
        attendee.params["PARTSTAT"]
        for attendee in event["ATTENDEE"]
        if str(attendee) == f"mailto:{email}"
    )


def test_answer_for_the_series_goes_to_an_exception_without_answer():
    outcomes, (series, exception) = _apply(_reply((None, "ACCEPTED", 0)))

    assert outcomes == ["applied"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "ACCEPTED"
    assert "RSVP" not in exception["ATTENDEE"][0].params
    assert _partstat(series, OTHER) == "NEEDS-ACTION"
    assert _partstat(exception, OTHER) == "NEEDS-ACTION"


def test_answer_for_the_series_keeps_a_different_answer_for_an_exception():
    stored = ICalendar.from_ical(SERIES)
    _apply(_reply((MOVED, "DECLINED", 1)), stored)

    outcomes, (series, exception) = _apply(_reply((None, "ACCEPTED", 0)), stored)

    assert outcomes == ["applied"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "DECLINED"


def test_new_answer_for_the_series_goes_to_an_exception_that_followed_it():
    stored = ICalendar.from_ical(SERIES)
    _apply(_reply((None, "ACCEPTED", 0)), stored)

    outcomes, (series, exception) = _apply(_reply((None, "DECLINED", 0)), stored)

    assert outcomes == ["applied"]
    assert _partstat(series) == "DECLINED"
    assert _partstat(exception) == "DECLINED"


def test_same_answer_for_the_series_goes_to_an_exception_without_answer():
    stored = ICalendar.from_ical(SERIES)
    stored.walk("VEVENT")[0]["ATTENDEE"][0].params["PARTSTAT"] = "ACCEPTED"

    outcomes, (series, exception) = _apply(_reply((None, "ACCEPTED", 0)), stored)

    assert outcomes == ["applied"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "ACCEPTED"


def test_stale_answer_for_the_series_does_not_change_an_exception():
    stored = ICalendar.from_ical(SERIES.replace("SEQUENCE:0", "SEQUENCE:2"))

    outcomes, (series, exception) = _apply(_reply((None, "ACCEPTED", 1)), stored)

    assert outcomes == ["stale-sequence"]
    assert _partstat(series) == "NEEDS-ACTION"
    assert _partstat(exception) == "NEEDS-ACTION"


def test_answer_for_one_occurrence_changes_only_its_exception():
    outcomes, (series, exception) = _apply(_reply((MOVED, "DECLINED", 1)))

    assert outcomes == ["applied"]
    assert _partstat(series) == "NEEDS-ACTION"
    assert _partstat(exception) == "DECLINED"
    assert _partstat(exception, OTHER) == "NEEDS-ACTION"


def test_reply_with_an_answer_for_the_series_and_for_an_exception():
    outcomes, (series, exception) = _apply(
        _reply((None, "ACCEPTED", 0), (MOVED, "TENTATIVE", 1))
    )

    assert outcomes == ["applied", "applied"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "TENTATIVE"


def test_answer_to_the_old_time_of_a_moved_occurrence_is_stale():
    outcomes, (series, exception) = _apply(
        _reply((None, "ACCEPTED", 0), (MOVED, "ACCEPTED", 0))
    )

    assert outcomes == ["applied", "stale-sequence"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "NEEDS-ACTION"


def test_answer_for_an_occurrence_without_exception_is_not_applied():
    outcomes, events = _apply(_reply((REGULAR, "DECLINED", 0)))

    assert outcomes == ["recurrence-not-supported"]
    assert len(events) == 2
    assert _partstat(events[0]) == "NEEDS-ACTION"


def test_same_answer_is_unchanged():
    stored = ICalendar.from_ical(SERIES)
    reply, _ = CalDAVService._parse_reply(_reply((MOVED, "DECLINED", 1)), ATTENDEE)
    service = CalDAVService(url="http://caldav.example/")

    assert service._apply_answers(stored, reply) == ["applied"]
    assert service._apply_answers(stored, reply) == ["unchanged"]


def test_applied_answer_is_recorded_as_sabredav_does():
    _, (series, _exception) = _apply(_reply((None, "ACCEPTED", 0)))

    attendee = series["ATTENDEE"][0]
    assert attendee.params["SCHEDULE-STATUS"] == "2.0"
    assert "RSVP" not in attendee.params
    assert series["ATTENDEE"][1].params["RSVP"] == "TRUE"


def test_parse_ignores_a_vevent_with_another_uid():
    ics = _reply((None, "ACCEPTED", 0)).replace(
        "END:VCALENDAR",
        "BEGIN:VEVENT\nUID:another@example.com\nDTSTART:20260302T090000Z\n"
        f"ATTENDEE;PARTSTAT=DECLINED:mailto:{ATTENDEE}\nEND:VEVENT\nEND:VCALENDAR",
    )

    reply, reason = CalDAVService._parse_reply(ics, ATTENDEE)

    assert reason is None
    assert reply["uid"] == "series@example.com"
    assert [answer["partstat"] for answer in reply["answers"]] == ["ACCEPTED"]


def test_parse_refuses_an_answer_for_a_range_of_occurrences():
    ics = _reply(("RECURRENCE-ID;RANGE=THISANDFUTURE:20260316T090000Z", "DECLINED", 0))

    assert CalDAVService._parse_reply(ics, ATTENDEE) == (
        None,
        "recurrence-not-supported",
    )


def test_parse_refuses_an_invalid_recurrence_id():
    ics = _reply(("RECURRENCE-ID;VALUE=DURATION:PT1H", "DECLINED", 0))

    assert CalDAVService._parse_reply(ics, ATTENDEE) == (None, "unparseable-reply")


def test_parse_reads_the_answer_in_upper_case():
    reply, _ = CalDAVService._parse_reply(_reply((None, "accepted", 0)), ATTENDEE)

    assert reply["answers"][0]["partstat"] == "ACCEPTED"


@pytest.mark.parametrize("partstat", ["DELEGATED", "NEEDS-ACTION", "X-MAYBE"])
def test_parse_refuses_an_answer_that_cannot_be_applied(partstat):
    ics = _reply((None, partstat, 0))

    assert CalDAVService._parse_reply(ics, ATTENDEE) == (None, "attendee-mismatch")


# A REPLY as SabreDAV writes it for an event that has no SEQUENCE.
SABREDAV_REPLY = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Sabre//Sabre VObject 4.6.0//EN
CALSCALE:GREGORIAN
METHOD:REPLY
TZID:Europe/Paris
BEGIN:VEVENT
UID:series@example.com
DTSTAMP:20260102T000000Z
SEQUENCE:
DTSTART;TZID=Europe/Paris;VALUE=DATE-TIME:20260302T100000
DTEND;TZID=Europe/Paris;VALUE=DATE-TIME:20260302T110000
SUMMARY:Weekly
ORGANIZER;CN=organizer:mailto:organizer@example.com
ATTENDEE;PARTSTAT=DECLINED:mailto:attendee@example.com
END:VEVENT
END:VCALENDAR"""


def test_parse_reads_an_empty_sequence_as_zero():
    reply, reason = CalDAVService._parse_reply(SABREDAV_REPLY, ATTENDEE)

    assert reason is None
    assert reply["answers"] == [
        {"recurrence_id": None, "partstat": "DECLINED", "sequence": 0}
    ]


def test_answer_with_an_empty_sequence_is_applied():
    outcomes, (series, _exception) = _apply(SABREDAV_REPLY)

    assert outcomes == ["applied"]
    assert _partstat(series) == "DECLINED"


def _outlook_reply(partstat, recurrence_id, start, end, sequence=0):
    """Build a REPLY for one occurrence as Outlook writes it.

    Outlook uses its own time zone name, keeps the RRULE of the series in the
    occurrence and writes MAILTO in upper case.
    """
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "METHOD:REPLY",
            "PRODID:Microsoft Exchange Server 2010",
            "VERSION:2.0",
            "BEGIN:VTIMEZONE",
            "TZID:Romance Standard Time",
            "BEGIN:STANDARD",
            "DTSTART:16010101T030000",
            "TZOFFSETFROM:+0200",
            "TZOFFSETTO:+0100",
            "RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=10",
            "END:STANDARD",
            "BEGIN:DAYLIGHT",
            "DTSTART:16010101T020000",
            "TZOFFSETFROM:+0100",
            "TZOFFSETTO:+0200",
            "RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=3",
            "END:DAYLIGHT",
            "END:VTIMEZONE",
            "BEGIN:VEVENT",
            f"ATTENDEE;PARTSTAT={partstat};CN=Attendee:MAILTO:{ATTENDEE}",
            "RRULE:FREQ=WEEKLY;COUNT=10;INTERVAL=1;BYDAY=MO;WKST=MO",
            "UID:series@example.com",
            f"RECURRENCE-ID;TZID=Romance Standard Time:{recurrence_id}",
            "SUMMARY;LANGUAGE=en-GB:Declined: Weekly",
            f"DTSTART;TZID=Romance Standard Time:{start}",
            f"DTEND;TZID=Romance Standard Time:{end}",
            "DTSTAMP:20260102T000000Z",
            "STATUS:CONFIRMED",
            f"SEQUENCE:{sequence}",
            "X-MICROSOFT-CDO-INSTTYPE:3",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


def test_outlook_answer_for_a_moved_occurrence_changes_its_exception():
    outcomes, (series, exception) = _apply(
        _outlook_reply(
            "TENTATIVE",
            "20260309T100000",
            "20260309T140000",
            "20260309T150000",
            sequence=1,
        )
    )

    assert outcomes == ["applied"]
    assert _partstat(series) == "NEEDS-ACTION"
    assert _partstat(exception) == "TENTATIVE"

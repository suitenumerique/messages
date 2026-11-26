"""Tests for how an iTIP REPLY changes the VEVENTs of a recurring event."""
# pylint: disable=protected-access, missing-function-docstring

from unittest import mock

from django.core.cache import cache

import pytest
from icalendar import Calendar as ICalendar

from core.services.calendar.service import (
    CalDAVError,
    CalDAVService,
    CalDAVTransientError,
)

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


def test_answer_to_the_old_time_of_a_moved_occurrence_is_applied():
    outcomes, (series, exception) = _apply(
        _reply((None, "ACCEPTED", 0), (MOVED, "DECLINED", 0))
    )

    assert outcomes == ["applied", "applied"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "DECLINED"


def test_answer_for_an_occurrence_without_exception_adds_one():
    outcomes, (series, moved, added) = _apply(_reply((REGULAR, "DECLINED", 0)))

    assert outcomes == ["applied"]
    assert _partstat(series) == "NEEDS-ACTION"
    assert _partstat(moved) == "NEEDS-ACTION"
    assert _partstat(added) == "DECLINED"
    assert _partstat(added, OTHER) == "NEEDS-ACTION"
    assert added["RECURRENCE-ID"].to_ical() == b"20260316T100000"
    assert added["RECURRENCE-ID"].params["TZID"] == "Europe/Paris"
    assert added["DTSTART"].to_ical() == b"20260316T100000"
    assert added["DTEND"].to_ical() == b"20260316T110000"
    assert added["SEQUENCE"] == 0
    assert "RRULE" not in added
    assert "RRULE" in series


def test_new_exception_has_no_schedule_status_from_the_series():
    stored = ICalendar.from_ical(
        SERIES.replace(
            "ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:other@example.com",
            "ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;ROLE=REQ-PARTICIPANT;CN=Other;"
            "SCHEDULE-STATUS=1.1:mailto:other@example.com",
        ).replace(
            "ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:attendee@example.com",
            "ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.1:"
            "mailto:attendee@example.com",
        )
    )

    outcomes, (series, moved, added) = _apply(_reply((REGULAR, "DECLINED", 0)), stored)

    assert outcomes == ["applied"]
    answered, other = added["ATTENDEE"]
    assert answered.params["SCHEDULE-STATUS"] == "2.0"
    assert "SCHEDULE-STATUS" not in other.params
    assert other.params["PARTSTAT"] == "NEEDS-ACTION"
    assert other.params["RSVP"] == "TRUE"
    assert other.params["ROLE"] == "REQ-PARTICIPANT"
    assert other.params["CN"] == "Other"
    for event in (series, moved):
        assert [a.params.get("SCHEDULE-STATUS") for a in event["ATTENDEE"]] == [
            "1.1",
            "1.1",
        ]


def test_no_exception_for_a_series_with_a_change_for_this_and_future():
    stored = ICalendar.from_ical(
        SERIES.replace(
            "RECURRENCE-ID;TZID=Europe/Paris:20260309T100000",
            "RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=Europe/Paris:20260309T100000",
        )
    )

    outcomes, events = _apply(_reply((REGULAR, "DECLINED", 0)), stored)

    assert outcomes == ["recurrence-not-supported"]
    assert len(events) == 2


def test_answer_for_one_date_does_not_change_a_change_for_this_and_future():
    stored = ICalendar.from_ical(
        SERIES.replace(
            "RECURRENCE-ID;TZID=Europe/Paris:20260309T100000",
            "RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=Europe/Paris:20260309T100000",
        )
    )

    outcomes, events = _apply(_reply((MOVED, "DECLINED", 0)), stored)

    assert outcomes == ["recurrence-not-supported"]
    assert _partstat(events[1]) == "NEEDS-ACTION"


def test_no_exception_for_a_series_with_an_exclusion_rule():
    # EXRULE removes each date until 23 March, also 16 March.
    stored = ICalendar.from_ical(
        SERIES.replace(
            "RRULE:FREQ=WEEKLY;COUNT=10",
            "RRULE:FREQ=WEEKLY;COUNT=10\nEXRULE:FREQ=WEEKLY;UNTIL=20260324T000000Z",
        )
    )

    outcomes, events = _apply(_reply((REGULAR, "DECLINED", 0)), stored)

    assert outcomes == ["recurrence-not-supported"]
    assert len(events) == 2


def test_no_exception_for_a_series_with_an_rdate_period():
    # The occurrence of 17 March lasts three hours, not one.
    stored = ICalendar.from_ical(
        SERIES.replace(
            "RRULE:FREQ=WEEKLY;COUNT=10",
            "RRULE:FREQ=WEEKLY;COUNT=10\nRDATE;VALUE=PERIOD:20260317T090000Z/PT3H",
        )
    )

    outcomes, events = _apply(
        _reply(("RECURRENCE-ID:20260317T090000Z", "DECLINED", 0)), stored
    )

    assert outcomes == ["recurrence-not-supported"]
    assert len(events) == 2


def test_transient_error_in_discovery_is_not_a_missing_event():
    service = CalDAVService(url="http://caldav.example/")

    with (
        mock.patch.object(
            service, "_discover_home_set", side_effect=CalDAVTransientError("503")
        ),
        pytest.raises(CalDAVTransientError),
    ):
        service.apply_reply(_reply((REGULAR, "DECLINED", 0)), ATTENDEE)

    assert service._home_set is None


@pytest.mark.parametrize("series_first", [True, False])
def test_reply_with_an_answer_for_the_series_and_for_an_occurrence_without_exception(
    series_first,
):
    answers = [(None, "ACCEPTED", 0), (REGULAR, "DECLINED", 0)]
    if not series_first:
        answers.reverse()

    outcomes, (series, moved, added) = _apply(_reply(*answers))

    assert outcomes == ["applied", "applied"]
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(moved) == "ACCEPTED"
    assert _partstat(added) == "DECLINED"
    assert _partstat(added, OTHER) == "NEEDS-ACTION"


def test_answer_for_an_occurrence_before_a_new_answer_for_the_series():
    stored = ICalendar.from_ical(SERIES)
    stored.walk("VEVENT")[0]["ATTENDEE"][0].params["PARTSTAT"] = "ACCEPTED"

    outcomes, (series, _, added) = _apply(
        _reply((REGULAR, "ACCEPTED", 0), (None, "DECLINED", 0)), stored
    )

    assert outcomes == ["applied", "applied"]
    assert _partstat(series) == "DECLINED"
    assert _partstat(added) == "ACCEPTED"


def test_answer_for_an_occurrence_that_repeats_the_series_answer_adds_nothing():
    stored = ICalendar.from_ical(SERIES)
    service = CalDAVService(url="http://caldav.example/")
    for ics in (_reply((None, "ACCEPTED", 0)), _reply((REGULAR, "ACCEPTED", 0))):
        reply, _ = CalDAVService._parse_reply(ics, ATTENDEE)
        outcomes = service._apply_answers(stored, reply)

    assert outcomes == ["unchanged"]
    assert len(stored.walk("VEVENT")) == 2


def test_parse_reads_a_limited_number_of_answers():
    answers = [(None, "ACCEPTED", 0)] * (CalDAVService._MAX_REPLY_ANSWERS + 5)

    reply, _ = CalDAVService._parse_reply(_reply(*answers), ATTENDEE)

    assert len(reply["answers"]) == CalDAVService._MAX_REPLY_ANSWERS


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


def test_parse_does_not_match_a_letter_that_lowers_to_ascii():
    # ``str.lower()`` maps U+212A KELVIN SIGN to "k".
    ics = _reply((None, "ACCEPTED", 0)).replace(ATTENDEE, "nick@example.com")

    assert CalDAVService._parse_reply(ics, "nic\u212a@example.com") == (
        None,
        "attendee-mismatch",
    )


RESOURCE = "http://caldav.example/cal/series.ics"


def _apply_to_resource(ics, stored, received_at=None, put=None):
    """Apply a REPLY to a stored resource. Return the result and the PUT mock."""
    service = CalDAVService(url="http://caldav.example/")
    reply, _reason = CalDAVService._parse_reply(ics, ATTENDEE)
    reply["received_at"] = received_at
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


@pytest.mark.usefixtures("reply_order")
def test_old_reply_for_the_series_is_applied_after_a_reply_for_one_occurrence():
    stored = ICalendar.from_ical(SERIES)
    _apply_to_resource(_reply((MOVED, "DECLINED", 1)), stored, received_at=200.0)

    result, _put = _apply_to_resource(
        _reply((None, "ACCEPTED", 0)), stored, received_at=100.0
    )

    series, exception = stored.walk("VEVENT")
    assert result == {"applied": True, "reason": "applied"}
    assert _partstat(series) == "ACCEPTED"
    assert _partstat(exception) == "DECLINED"


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


def test_write_is_not_tried_again_without_an_etag():
    service = CalDAVService(url="http://caldav.example/")
    reply, _reason = CalDAVService._parse_reply(_reply((None, "ACCEPTED", 0)), ATTENDEE)
    conflict = CalDAVError("precondition failed", status_code=412)
    with (
        mock.patch.object(service, "_put_resource", side_effect=conflict) as put,
        mock.patch.object(service, "_get_resource", return_value=(SERIES, None)),
    ):
        result = service._apply_reply_to_resource(
            RESOURCE, ICalendar.from_ical(SERIES), '"1"', reply
        )

    assert result == {"applied": False, "reason": "no-etag"}
    assert put.call_count == 1


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


def test_parse_reads_a_reply_with_an_empty_sequence():
    reply, reason = CalDAVService._parse_reply(SABREDAV_REPLY, ATTENDEE)

    assert reason is None
    assert reply["answers"] == [{"recurrence_id": None, "partstat": "DECLINED"}]


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


def test_outlook_answer_with_an_older_sequence_adds_an_exception():
    # The organizer changed the attendees of the series twice. SabreDAV did not
    # send these updates to this attendee, so Outlook still has SEQUENCE 2.
    stored = ICalendar.from_ical(SERIES.replace("SEQUENCE:0", "SEQUENCE:4", 1))
    ics = _outlook_reply(
        "DECLINED", "20260330T100000", "20260330T100000", "20260330T110000", 2
    ).replace("TZID=Romance Standard Time", 'TZID="Romance Standard Time"')

    outcomes, (series, _, added) = _apply(ics, stored)

    assert outcomes == ["applied"]
    assert _partstat(series) == "NEEDS-ACTION"
    assert _partstat(added) == "DECLINED"
    assert added["SEQUENCE"] == 4
    assert added["RECURRENCE-ID"].to_ical() == b"20260330T100000"
    assert added["RECURRENCE-ID"].params["TZID"] == "Europe/Paris"


def test_open_xchange_answer_for_an_excluded_date_is_ignored(caplog):
    stored = ICalendar.from_ical(
        SERIES.replace(
            "RRULE:FREQ=WEEKLY;COUNT=10",
            "RRULE:FREQ=WEEKLY;COUNT=10\nEXDATE;TZID=Europe/Paris:20260323T100000",
        )
    )
    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Open-Xchange//8.0//EN",
            "METHOD:REPLY",
            "BEGIN:VEVENT",
            "UID:series@example.com",
            "DTSTAMP:20260320T000000Z",
            "RECURRENCE-ID;TZID=Europe/Paris:20260323T100000",
            "DTSTART;TZID=Europe/Paris:20260323T100000",
            "DTEND;TZID=Europe/Paris:20260323T110000",
            "SEQUENCE:0",
            "ORGANIZER:mailto:organizer@example.com",
            f"ATTENDEE;PARTSTAT=DECLINED:mailto:{ATTENDEE}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )

    with caplog.at_level("INFO", logger="core.services.calendar.service"):
        outcomes, events = _apply(ics, stored)

    assert outcomes == ["no-matching-occurrence"]
    assert len(events) == 2
    assert _partstat(events[0]) == "NEEDS-ACTION"
    assert "not an occurrence of UID series@example.com" in caplog.text


def test_sabredav_answer_for_an_occurrence_without_exception_adds_one():
    # Built as Broker::createReply in sabre/vobject does: the VTIMEZONE of
    # the event, the SEQUENCE of the series, and the DTSTART of the occurrence.
    stored = ICalendar.from_ical(SERIES.replace("SEQUENCE:0", "SEQUENCE:2", 1))
    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Sabre//Sabre VObject 4.5.6//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:REPLY",
            "BEGIN:VTIMEZONE",
            "TZID:Europe/Paris",
            "BEGIN:DAYLIGHT",
            "TZOFFSETFROM:+0100",
            "TZOFFSETTO:+0200",
            "TZNAME:CEST",
            "DTSTART:19700329T020000",
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
            "END:DAYLIGHT",
            "BEGIN:STANDARD",
            "TZOFFSETFROM:+0200",
            "TZOFFSETTO:+0100",
            "TZNAME:CET",
            "DTSTART:19701025T030000",
            "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
            "END:STANDARD",
            "END:VTIMEZONE",
            "BEGIN:VEVENT",
            "UID:series@example.com",
            "SEQUENCE:2",
            "DTSTAMP:20260320T000000Z",
            "DTSTART;TZID=Europe/Paris:20260330T100000",
            "DTEND;TZID=Europe/Paris:20260330T110000",
            "SUMMARY:Weekly",
            "RECURRENCE-ID;TZID=Europe/Paris:20260330T100000",
            "ORGANIZER;CN=Organizer:mailto:organizer@example.com",
            f"ATTENDEE;PARTSTAT=ACCEPTED;CN=Attendee:mailto:{ATTENDEE}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )

    outcomes, (series, _, added) = _apply(ics, stored)

    assert outcomes == ["applied"]
    assert _partstat(series) == "NEEDS-ACTION"
    assert _partstat(added) == "ACCEPTED"
    assert added["SEQUENCE"] == 2
    assert added["DTSTART"].to_ical() == b"20260330T100000"

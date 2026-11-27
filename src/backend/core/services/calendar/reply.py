"""Read an iTIP REPLY and apply its answers to the stored event."""

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.services.calendar.attendees import attendee_key, attendees_of, partstat_of
from core.services.calendar.recurrence import (
    has_rdate_period,
    occurrence_override,
    recurrence_key,
)
from core.services.calendar.untrusted_ics import parse_untrusted_ics

logger = logging.getLogger(__name__)

# Answers that a REPLY can give. A delegation (DELEGATED) is not supported.
_REPLY_PARTSTATS = frozenset({"ACCEPTED", "DECLINED", "TENTATIVE"})

# Max number of answers read from one reply. An answer for an occurrence can
# add an exception VEVENT to the stored event.
MAX_REPLY_ANSWERS = 100
MAX_UID_CHARS = 255
# A reply adds at most one VEVENT for each date. Each is a copy of the series,
# so the event must not grow without limit.
MAX_EVENT_COMPONENTS = 500


class ReplyOutcome(StrEnum):
    """What a reply, or one of its answers, does to the stored event."""

    APPLIED = "applied"
    UNCHANGED = "unchanged"
    SUPERSEDED = "superseded"
    UNPARSEABLE_REPLY = "unparseable-reply"
    ATTENDEE_MISMATCH = "attendee-mismatch"
    ATTENDEE_NOT_ON_EVENT = "attendee-not-on-event"
    NO_MATCHING_EVENT = "no-matching-event"
    NO_MATCHING_OCCURRENCE = "no-matching-occurrence"
    RECURRENCE_NOT_SUPPORTED = "recurrence-not-supported"
    TOO_MANY_EXCEPTIONS = "too-many-exceptions"
    NO_ETAG = "no-etag"
    CONCURRENT_MODIFICATION = "concurrent-modification"


@dataclass(frozen=True)
class Answer:
    """The answer of the attendee for the series, or for one occurrence.

    ``recurrence_id`` is the original start of the occurrence, or None for
    the series.
    """

    recurrence_id: date | None
    partstat: str


@dataclass(frozen=True)
class Reply:
    """The answers of one attendee in an iTIP REPLY.

    ``received_at`` is the time when the mailbox received the reply, in
    seconds, or None. ``organizer`` is the address of the organizer that the
    stored event must have, or an empty string for any organizer.
    """

    uid: str
    attendee: str
    answers: tuple[Answer, ...]
    received_at: float | None = None
    organizer: str = ""


def _date_value(prop):
    """Return the date or date-time of a property, or None when it has none."""
    value = getattr(prop, "dt", None)
    return value if isinstance(value, date) else None


def _occurrence_key(recurrence_id):
    """Return the key of the occurrence, or None when there is no RECURRENCE-ID."""
    return None if recurrence_id is None else recurrence_key(recurrence_id)


def parse_reply(ics_data, attendee_email):
    """Read the answers of ``attendee_email`` from a REPLY.

    Returns ``(reply, None)``, or ``(None, reason)`` when the REPLY is not
    usable. The reply has one answer for each VEVENT where the attendee
    gives a PARTSTAT. The PARTSTAT of other attendees is never read. The
    first of these VEVENTs gives the UID. A VEVENT with another UID gives
    no answer.

    The reasons are:

    - ``unparseable-reply``: the ICS is bad, no VEVENT has a UID of at
      most 255 characters, or a RECURRENCE-ID is invalid.
    - ``attendee-mismatch``: the attendee gives no ACCEPTED, DECLINED or
      TENTATIVE.
    - ``recurrence-not-supported``: an answer is for a range of
      occurrences.
    """
    target = attendee_key(attendee_email)
    if not target:
        return None, ReplyOutcome.ATTENDEE_MISMATCH
    try:
        cal = parse_untrusted_ics(ics_data)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("Could not parse inbound iTIP REPLY", exc_info=True)
        return None, ReplyOutcome.UNPARSEABLE_REPLY
    reply_uid = None
    answers = []
    saw_uid = False
    for comp in cal.walk("VEVENT"):
        uid = comp.get("UID")
        # SabreDAV keeps at most 200 characters of a UID.
        if not uid or len(str(uid)) > MAX_UID_CHARS:
            continue
        saw_uid = True
        if reply_uid is not None and str(uid) != reply_uid:
            continue
        attendees = comp.get("ATTENDEE")
        if attendees is None:
            continue
        if not isinstance(attendees, list):
            attendees = [attendees]
        partstat = None
        for att in attendees:
            if attendee_key(att) == target:
                partstat = str(att.params.get("PARTSTAT") or "").upper()
                break
        if partstat not in _REPLY_PARTSTATS:
            continue
        recurrence_id = comp.get("RECURRENCE-ID")
        if recurrence_id is not None:
            if _date_value(recurrence_id) is None:
                return None, ReplyOutcome.UNPARSEABLE_REPLY
            if recurrence_id.params.get("RANGE"):
                return None, ReplyOutcome.RECURRENCE_NOT_SUPPORTED
        reply_uid = str(uid)
        if len(answers) == MAX_REPLY_ANSWERS:
            break
        answers.append(Answer(_date_value(recurrence_id), partstat))
    if answers:
        return Reply(uid=reply_uid, attendee=target, answers=tuple(answers)), None
    if saw_uid:
        return None, ReplyOutcome.ATTENDEE_MISMATCH
    return None, ReplyOutcome.UNPARSEABLE_REPLY


def apply_answers(stored, reply):
    """Apply each answer of the reply to its VEVENT in ``stored``, in-place.

    Returns the outcome of each answer: ``applied``, or the reason why the
    answer changes nothing.
    """
    events = dict(reply_events(stored, reply))

    answers = [
        (_occurrence_key(answer.recurrence_id), answer) for answer in reply.answers
    ]
    named = {key for key, _ in answers}

    attendee = reply.attendee
    outcomes = [None] * len(answers)
    # The series goes first. A new exception is a copy of the series, so it
    # must contain the new answer of the series.
    for index in sorted(range(len(answers)), key=lambda i: answers[i][0] is not None):
        key, answer = answers[index]
        if key is None and None not in events:
            outcome = ReplyOutcome.NO_MATCHING_EVENT
        elif key is None:
            outcome = _apply_series_answer(events, named, attendee, answer)
        elif key in events and events[key]["RECURRENCE-ID"].params.get("RANGE"):
            # This entry also holds the later occurrences.
            outcome = ReplyOutcome.RECURRENCE_NOT_SUPPORTED
        elif key in events:
            outcome = set_partstat(events[key], attendee, answer.partstat)
        else:
            outcome = _add_exception(stored, events, attendee, answer)
        outcomes[index] = outcome
    return outcomes


def _add_exception(stored, events, attendee_email, answer):
    """Add an exception that holds the answer for one occurrence, in-place.

    Returns ``applied``, or the reason why the stored event does not change.
    """
    recurrence_id = answer.recurrence_id
    series = events.get(None)
    # An entry with a RANGE also changes the later occurrences, and an
    # RDATE period has its own end. A copy of the series does not contain
    # these changes. The expansion does not read EXRULE, which RFC 5545
    # deprecates.
    if any(
        event.get("RECURRENCE-ID") is not None
        and event["RECURRENCE-ID"].params.get("RANGE")
        for event in events.values()
    ) or (series is not None and (has_rdate_period(series) or "EXRULE" in series)):
        return ReplyOutcome.RECURRENCE_NOT_SUPPORTED
    if len(stored.subcomponents) >= MAX_EVENT_COMPONENTS:
        return ReplyOutcome.TOO_MANY_EXCEPTIONS
    exception = series and occurrence_override(series, recurrence_id)
    if exception is None:
        logger.info(
            "Ignoring iTIP REPLY answer for %s: not an occurrence of UID %r",
            recurrence_id.isoformat(),
            series and str(series.get("UID")),
        )
        return ReplyOutcome.NO_MATCHING_OCCURRENCE
    outcome = set_partstat(exception, attendee_email, answer.partstat)
    # An exception that changes nothing has no use.
    if outcome == ReplyOutcome.APPLIED:
        stored.add_component(exception)
        events[_occurrence_key(recurrence_id)] = exception
    return outcome


def _apply_series_answer(events, skipped, attendee_email, answer):
    """Set the answer of the attendee on the series, in-place.

    The answer also goes to each exception that is not in ``skipped``, the
    occurrences that the reply names. Some clients, such as Outlook, send
    only the series VEVENT to answer for all the occurrences. An exception
    takes the answer when the attendee has no answer there, or has the
    answer that the series had. A different answer for one occurrence
    stays.

    Returns ``applied`` when a VEVENT changes, else the reason why the
    series does not change.
    """
    series = events[None]
    before = partstat_of(series, attendee_email)
    outcome = set_partstat(series, attendee_email, answer.partstat)
    if outcome not in (ReplyOutcome.APPLIED, ReplyOutcome.UNCHANGED):
        return outcome
    for key, exception in events.items():
        if key in skipped:
            continue
        current = partstat_of(exception, attendee_email)
        if current not in ("NEEDS-ACTION", before):
            continue
        changed = set_partstat(exception, attendee_email, answer.partstat)
        if changed == ReplyOutcome.APPLIED:
            outcome = ReplyOutcome.APPLIED
    return outcome


def set_partstat(event, attendee_email, partstat):
    """Set the PARTSTAT of the attendee on one VEVENT, in-place.

    Returns ``applied``, or the reason why the VEVENT does not change. Like
    SabreDAV, records the answer with SCHEDULE-STATUS=2.0 and removes RSVP.
    """
    attendees = attendees_of(event, attendee_email)
    if not attendees:
        return ReplyOutcome.ATTENDEE_NOT_ON_EVENT
    if all(str(att.params.get("PARTSTAT") or "") == partstat for att in attendees):
        return ReplyOutcome.UNCHANGED
    for att in attendees:
        att.params["PARTSTAT"] = partstat
        att.params.pop("RSVP", None)
        att.params["SCHEDULE-STATUS"] = "2.0"
    return ReplyOutcome.APPLIED


def reply_events(stored, reply):
    """Yield ``(key, VEVENT)`` for each VEVENT of the event of the reply."""
    for comp in stored.walk("VEVENT"):
        if str(comp.get("UID") or "") != reply.uid:
            continue
        recurrence_id = comp.get("RECURRENCE-ID")
        key = None if recurrence_id is None else recurrence_key(recurrence_id.dt)
        yield key, comp


def answers_by_key(stored, reply):
    """Return the answer of the attendee on each VEVENT, by key."""
    return {
        key: partstat_of(event, reply.attendee)
        for key, event in reply_events(stored, reply)
    }

"""Occurrences of a recurring VEVENT."""

import copy
import itertools
from datetime import datetime, time, timezone

from dateutil.rrule import rruleset, rrulestr
from icalendar import vDDDTypes

# Number of occurrences examined from the start of a series. The searched date
# comes from an email, so the search must have a limit. A daily series reaches
# this limit after 54 years.
_MAX_OCCURRENCES = 20_000

_RECURRENCE_PROPERTIES = ("RRULE", "RDATE", "EXDATE", "EXRULE")


def recurrence_key(value):
    """Return a key that identifies an occurrence by its original start.

    Two date-times with a timezone give the same key when they are the same
    instant, in any timezone. A date and a date-time without timezone compare
    by value. Values of different kinds never give the same key.
    """
    if not isinstance(value, datetime):
        return ("date", value)
    if value.tzinfo is None:
        return ("floating", value)
    return ("instant", value.astimezone(timezone.utc))


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def has_rdate_period(master):
    """Tell if an RDATE of the series is a period, with its own end."""
    return any(
        isinstance(item.dt, tuple)
        for prop in _as_list(master.get("RDATE"))
        for item in prop.dts
    )


def _widen(value):
    """Return a date as a date-time, because dateutil computes with date-times."""
    return value if isinstance(value, datetime) else datetime.combine(value, time.min)


def _occurrences(master):
    """Yield the start of each occurrence of the series, in order."""
    start = master["DTSTART"].dt
    all_day = not isinstance(start, datetime)

    rules = rruleset()
    # DTSTART is always an occurrence, also when the rule does not match it.
    rules.rdate(_widen(start))
    for rrule in _as_list(master.get("RRULE")):
        rules.rrule(rrulestr(rrule.to_ical().decode(), dtstart=_widen(start)))
    for name, add in (("RDATE", rules.rdate), ("EXDATE", rules.exdate)):
        for prop in _as_list(master.get(name)):
            for item in prop.dts:
                # An RDATE can also be a period, which is a tuple.
                if isinstance(item.dt, tuple):
                    continue
                add(_widen(item.dt))
    for occurrence in rules:
        yield occurrence.date() if all_day else occurrence


def _find_occurrence(master, recurrence_id):
    """Return the start of the occurrence of the series at ``recurrence_id``.

    Returns None when the series has no occurrence at that time.
    """
    target = recurrence_key(recurrence_id)
    for occurrence in itertools.islice(_occurrences(master), _MAX_OCCURRENCES):
        key = recurrence_key(occurrence)
        if key == target:
            return occurrence
        # Values of different kinds do not compare. The occurrences are in
        # order, so a later one ends the search.
        if key[0] != target[0] or key[1] > target[1]:
            return None
    return None


def _set_date(component, name, value, like):
    """Set a date property with the parameters (TZID, VALUE) of ``like``.

    A date-time with a timezone changes to the timezone of ``like`` first.
    """
    like_tz = getattr(like.dt, "tzinfo", None)
    if isinstance(value, datetime) and value.tzinfo is not None and like_tz:
        value = value.astimezone(like_tz)
    prop = vDDDTypes(value)
    prop.params = copy.deepcopy(like.params)
    component[name] = prop


def _end(start, master_start, master_end):
    """Return the end of the occurrence at ``start``.

    The occurrence has the exact duration of the series (RFC 5545 3.8.5.3).
    Python subtracts two date-times with the same timezone in wall-clock time,
    so the calculation uses UTC.
    """
    if isinstance(start, datetime) and start.tzinfo is not None:
        duration = master_end.astimezone(timezone.utc) - master_start.astimezone(
            timezone.utc
        )
        return start.astimezone(timezone.utc) + duration
    return start + (master_end - master_start)


def occurrence_override(master, recurrence_id):
    """Return a new exception VEVENT for one occurrence of a series.

    ``recurrence_id`` is the original start of the occurrence. The exception is
    a copy of ``master`` at the time of the occurrence, without the recurrence
    rule. Returns None when the event does not repeat, when it has no
    occurrence at that time, or when its rule cannot be expanded.
    """
    if master.get("RRULE") is None and master.get("RDATE") is None:
        return None
    try:
        start = _find_occurrence(master, recurrence_id)
    except (ValueError, TypeError):
        # dateutil refuses a rule that mixes values with and without timezone.
        return None
    if start is None:
        return None

    override = copy.deepcopy(master)
    for name in _RECURRENCE_PROPERTIES:
        override.pop(name, None)
    # No message went to the attendees for the new exception.
    for attendee in _as_list(override.get("ATTENDEE")):
        attendee.params.pop("SCHEDULE-STATUS", None)
    master_start = master["DTSTART"]
    _set_date(override, "DTSTART", start, master_start)
    _set_date(override, "RECURRENCE-ID", start, master_start)
    master_end = master.get("DTEND")
    if master_end is not None:
        _set_date(
            override, "DTEND", _end(start, master_start.dt, master_end.dt), master_end
        )
    return override

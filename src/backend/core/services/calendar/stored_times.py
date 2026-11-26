"""Read the times of a stored event or of one of its occurrences."""

import itertools
import re
from datetime import date, datetime, time, timedelta, timezone

from dateutil.rrule import rruleset, rrulestr
from icalendar import Calendar as ICalendar
from icalendar import Event, IncompleteComponent, vDDDTypes

# Number of occurrences examined from the start of a series. The searched date
# comes from an email, so the search must have a limit.
_MAX_OCCURRENCES = 20_000


def _instant_key(value: date | datetime) -> date | datetime:
    """Comparable form of a date or date-time value. A zoned time is in UTC."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value


def _widen(value: date | datetime) -> datetime:
    """Return a date as a date-time, because dateutil computes with date-times."""
    return value if isinstance(value, datetime) else datetime.combine(value, time.min)


def _add_exact(value: date | datetime, delta: timedelta) -> date | datetime:
    """Add an exact time span. A zoned time keeps its zone."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return (value.astimezone(timezone.utc) + delta).astimezone(value.tzinfo)
    return value + delta


def _raw_durations(ics_text: str, uid: str) -> list[str | None]:
    """Return the DURATION of each VEVENT ``uid`` as written in the ICS.

    The list follows the order of the VEVENTs in the ICS. icalendar writes
    PT24H back as P1D. The units decide which part of the duration is
    nominal, so the code reads the original text.
    """
    # The open components, innermost last. Only the direct properties of a
    # VEVENT count: a VALARM inside it can have its own DURATION.
    components: list[str] = []
    fields: dict[str, str] = {}
    durations: list[str | None] = []
    for line in re.sub(r"\r?\n[ \t]", "", ics_text).splitlines():
        name, _, value = line.partition(":")
        name = name.split(";")[0].upper()
        if name == "BEGIN":
            components.append(value.upper())
            if components[-1] == "VEVENT":
                fields = {}
        elif name == "END":
            if components and components.pop() == "VEVENT" and fields.get("UID") == uid:
                durations.append(fields.get("DURATION"))
        elif components and components[-1] == "VEVENT":
            fields[name] = value
    return durations


def _end_from_start(
    series: Event, start: date | datetime, raw_duration: str | None
) -> date | datetime:
    """Return the end of an occurrence of ``series`` that starts at ``start``.

    RFC 5545 section 3.8.5.3: a DTEND gives the same exact duration to each
    occurrence. The days and weeks of a DURATION are nominal, so they keep
    the wall-clock time across a DST change. The other parts are exact.
    ``raw_duration`` is the DURATION value as written in the ICS.
    """
    if (
        isinstance(start, datetime)
        and start.tzinfo is not None
        and isinstance(series.start, datetime)
        and series.start.tzinfo is not None
    ):
        start = start.astimezone(series.start.tzinfo)
    if "DTEND" in series:
        exact = _instant_key(series.end) - _instant_key(series.start)
        return _add_exact(start, exact)
    if "DURATION" not in series or raw_duration is None:
        return start + series.duration
    duration: timedelta = series["DURATION"].dt
    weeks = re.search(r"(\d+)W", raw_duration)
    days = re.search(r"(\d+)D", raw_duration)
    nominal = timedelta(
        weeks=int(weeks.group(1)) if weeks else 0,
        days=int(days.group(1)) if days else 0,
    )
    return _add_exact(start + nominal, duration - nominal)


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _occurrence_end(
    series: Event, start: date | datetime, raw_duration: str | None
) -> date | datetime | None:
    """Return the end of the occurrence of the series that starts at ``start``.

    RRULE, RDATE and EXDATE decide. An RDATE period gives its own end. Any
    other occurrence lasts as long as the series event. The result is None
    when the series has no occurrence at ``start``. A value of another kind
    than DTSTART matches no occurrence.
    """
    dtstart = _widen(series.start)
    rules = rruleset()
    rules.rdate(dtstart)
    for rrule in _as_list(series.get("RRULE")):
        rules.rrule(rrulestr(rrule.to_ical().decode(), dtstart=dtstart))
    period_ends: dict[datetime, date | datetime] = {}
    for prop in _as_list(series.get("RDATE")):
        for item in prop.dts:
            # A period is a tuple: a start, and an end or a duration.
            if isinstance(item.dt, tuple):
                period_start, end_or_duration = item.dt
                if isinstance(end_or_duration, timedelta):
                    end_or_duration = period_start + end_or_duration
                period_ends[_widen(period_start)] = end_or_duration
                rules.rdate(_widen(period_start))
            else:
                rules.rdate(_widen(item.dt))
    for prop in _as_list(series.get("EXDATE")):
        for item in prop.dts:
            rules.exdate(_widen(item.dt))
    target = _widen(start)
    try:
        for occurrence in itertools.islice(rules, _MAX_OCCURRENCES):
            if occurrence == target:
                return period_ends.get(target) or _end_from_start(
                    series, start, raw_duration
                )
            if occurrence > target:
                return None
    except TypeError:
        # A date-time with a time zone does not compare with one without.
        return None
    return None


def _times(start: date | datetime, end: date | datetime) -> dict[str, str | bool]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": not isinstance(start, datetime),
    }


def stored_times(
    ics_text: str, uid: str, recurrence_id: vDDDTypes | None
) -> dict[str, str | bool] | None:
    """Return the times of the event ``uid`` in ``ics_text``.

    With ``recurrence_id``, return the times of that occurrence. A changed
    occurrence has its own VEVENT. Any other occurrence starts at its
    RECURRENCE-ID. The result is None when the series has no occurrence at
    the RECURRENCE-ID, or when a RANGE=THISANDFUTURE override applies.
    """
    try:
        events = [
            event
            for event in ICalendar.from_ical(ics_text).walk("VEVENT")
            if str(event.get("UID")) == uid
        ]
        raw_durations = _raw_durations(ics_text, uid)
        if len(raw_durations) != len(events):
            raw_durations = [None] * len(events)
        raw_by_event = {
            id(e): raw for e, raw in zip(events, raw_durations, strict=True)
        }

        def own_times(event: Event) -> dict[str, str | bool]:
            end = _end_from_start(event, event.start, raw_by_event[id(event)])
            return _times(event.start, end)

        if recurrence_id is None:
            series = next(e for e in events if e.get("RECURRENCE-ID") is None)
            return own_times(series)
        key = _instant_key(recurrence_id.dt)
        for event in events:
            stored_id = event.get("RECURRENCE-ID")
            if stored_id is not None and _instant_key(stored_id.dt) == key:
                return own_times(event)
        # A RANGE=THISANDFUTURE override also moves the later occurrences.
        # SabreDAV and Calendars do not write RANGE, so it is rare. The time
        # is then unknown and not guessed.
        for event in events:
            stored_id = event.get("RECURRENCE-ID")
            if (
                stored_id is not None
                and stored_id.params.get("RANGE") == "THISANDFUTURE"
                and _instant_key(stored_id.dt) <= key
            ):
                return None
        series = next(e for e in events if e.get("RECURRENCE-ID") is None)
        end = _occurrence_end(series, recurrence_id.dt, raw_by_event[id(series)])
        if end is None:
            return None
        return _times(recurrence_id.dt, end)
    except (StopIteration, IncompleteComponent, ValueError, TypeError):
        return None

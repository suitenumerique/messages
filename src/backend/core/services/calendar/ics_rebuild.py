"""Storage-safe ICS rebuild.

Inbound .ics is attacker-controlled (it arrives as an email attachment).
Rather than try to enumerate every dangerous extension to strip — METHOD,
VALARM with ACTION:EMAIL (Apple amplification), X-MS-OLK-*, X-ALT-DESC
HTML, ATTACH with data: URIs, future iCal extensions we haven't heard
of — we rebuild a fresh VCALENDAR from a tight allowlist of RFC 5545
properties before PUTing to the CalDAV server.
"""

import copy
import re
from datetime import date, datetime, timezone
from datetime import time as dt_time

from icalendar import Calendar as ICalendar
from icalendar import Event as ICalEvent

# VEVENT properties kept on the rebuilt event. Everything else is dropped:
# notable exclusions are ATTACH (size + scheme abuse), GEO, RESOURCES,
# RELATED-TO, REQUEST-STATUS, COMMENT, any X-* extension.
_VEVENT_KEEP = frozenset(
    {
        # Identity / iTIP versioning
        "UID",
        "DTSTAMP",
        "SEQUENCE",
        "CREATED",
        "LAST-MODIFIED",
        # Time / recurrence
        "DTSTART",
        "DTEND",
        "DURATION",
        "RRULE",
        "RDATE",
        "EXDATE",
        "RECURRENCE-ID",
        # Display
        "SUMMARY",
        "DESCRIPTION",
        "LOCATION",
        "URL",
        "CATEGORIES",
        # Semantics
        "STATUS",
        "TRANSP",
        "CLASS",
        "PRIORITY",
        "ORGANIZER",
        "ATTENDEE",
    }
)

# Per-property parameter allowlist. Anything else (X-*, unknown future
# params, attacker-crafted noise) is dropped. Properties not in this map
# get all parameters stripped.
_PARAM_KEEP = {
    "DTSTART": frozenset({"TZID", "VALUE"}),
    "DTEND": frozenset({"TZID", "VALUE"}),
    "DURATION": frozenset(),
    "RECURRENCE-ID": frozenset({"TZID", "VALUE", "RANGE"}),
    "RDATE": frozenset({"TZID", "VALUE"}),
    "EXDATE": frozenset({"TZID", "VALUE"}),
    "ATTENDEE": frozenset(
        {
            "CN",
            "PARTSTAT",
            "ROLE",
            "CUTYPE",
            "RSVP",
            "MEMBER",
            "DELEGATED-TO",
            "DELEGATED-FROM",
            "SENT-BY",
            "DIR",
            "LANGUAGE",
            "SCHEDULE-AGENT",
            "SCHEDULE-STATUS",
        }
    ),
    "ORGANIZER": frozenset(
        {
            "CN",
            "DIR",
            "SENT-BY",
            "LANGUAGE",
            "SCHEDULE-AGENT",
            "SCHEDULE-STATUS",
        }
    ),
    "SUMMARY": frozenset({"LANGUAGE"}),
    "DESCRIPTION": frozenset({"LANGUAGE"}),
    "LOCATION": frozenset({"LANGUAGE"}),
    "CATEGORIES": frozenset({"LANGUAGE"}),
}

# URL property must start with http:// or https://. We don't use
# ``urlparse`` here — browsers tolerate whitespace, control chars and
# weird Unicode that urlparse rejects, so a string we'd consider "safe"
# (because urlparse couldn't extract a dangerous scheme) might still
# resolve to ``javascript:`` in the browser. Be stricter than urlparse:
# the value must literally start with ``http://`` or ``https://``
# (case-insensitive, no leading whitespace).
_SAFE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# RRULE frequencies that produce ruinous expansion if unbounded.
# Thunderbird hard-froze on a SECONDLY-frequency invite without
# COUNT/UNTIL (Mozilla bug 1770984). Reject these unless bounded by a
# small COUNT.
_RRULE_FREQ_REQUIRES_BOUND = frozenset({"SECONDLY", "MINUTELY"})
_SUBHOURLY_MAX_COUNT = 1000

# Date properties RFC 5545 allows once per VEVENT.
_SINGLE_DATES = frozenset(
    {"DTSTAMP", "CREATED", "LAST-MODIFIED", "DTSTART", "DTEND", "RECURRENCE-ID"}
)
# Date properties that define which occurrences exist.
_RECURRENCE_DATES = frozenset({"RECURRENCE-ID", "RDATE", "EXDATE"})


class UnstorableCalendarError(ValueError):
    """The calendar has an event that cannot be stored faithfully."""


def rebuild_for_storage(cal):
    """Return a fresh VCALENDAR containing only allowlisted properties.

    Raises ``UnstorableCalendarError`` when there is no event, or when an
    event has to be dropped: the events of a calendar are the master and
    exceptions of one series, which is stored whole or not at all.

    Default-deny posture: inbound .ics is attacker-controlled (it
    arrives as an email attachment), so rather than try to enumerate
    every dangerous extension to strip — METHOD, VALARM with
    ACTION:EMAIL (Apple amplification), X-MS-OLK-*, X-ALT-DESC HTML,
    ATTACH with data: URIs, future iCal extensions we haven't heard
    of — we rebuild a fresh calendar from a small allowlist of
    well-understood RFC 5545 properties.

    VTIMEZONE blocks referenced by a kept event's ``TZID`` parameter
    are preserved so events authored in non-UTC zones still render
    correctly. VTIMEZONEs not referenced by any kept event are
    dropped.

    See _VEVENT_KEEP / _PARAM_KEEP for the exact allowlist.
    """
    # Work on a deep copy throughout: ``_filter_params`` mutates the
    # value objects' ``params`` dicts, and we add components by
    # reference to ``fresh``. Without the copy the caller's parsed
    # cal would be silently mutilated after this returns.
    cal = copy.deepcopy(cal)

    fresh = ICalendar()
    # Always stamp our own PRODID. Preserving the input's would echo
    # attacker branding ("Created by Evil Corp") into the user's
    # calendar and is informationally useless for storage. VERSION
    # is fixed at 2.0 (RFC 5545); CALSCALE is preserved when present.
    fresh.add("PRODID", "-//messages//CalDAV interop//EN")
    fresh.add("VERSION", "2.0")
    if cal.get("CALSCALE"):
        fresh.add("CALSCALE", str(cal["CALSCALE"]))

    # Pass 1: rebuild every VEVENT and learn which TZIDs they
    # actually reference.
    rebuilt_events = []
    referenced_tzids = set()
    for vevent in cal.walk("VEVENT"):
        clean = _rebuild_event(vevent)
        if clean is None:
            raise UnstorableCalendarError("An event has no UID or unreadable dates.")
        for prop in ("DTSTART", "DTEND", "RECURRENCE-ID", "RDATE", "EXDATE"):
            val = clean.get(prop)
            if val is None:
                continue
            for v in val if isinstance(val, list) else [val]:
                tzid = getattr(v, "params", {}).get("TZID")
                if tzid:
                    referenced_tzids.add(str(tzid))
        rebuilt_events.append(clean)
    if not rebuilt_events:
        raise UnstorableCalendarError("The calendar has no event.")

    # Pass 2: preserve only the VTIMEZONEs our kept events use.
    for vtz in cal.walk("VTIMEZONE"):
        if str(vtz.get("TZID") or "") in referenced_tzids:
            fresh.add_component(vtz)

    for evt in rebuilt_events:
        fresh.add_component(evt)

    return fresh


def _rebuild_event(src):
    """Build a fresh VEVENT containing only allowlisted properties.

    Returns ``None`` if the source has no UID (malformed event per
    RFC 5545), no readable DTSTART, or a recurrence date that cannot be
    read. A missing DTSTAMP is synthesized (see fallback chain below).

    DTSTAMP caveat: iTIP sequencing (RFC 5546 §3.2.6) compares
    SEQUENCE first, then DTSTAMP, to decide whether an inbound
    update supersedes what's already stored. The mainstream
    generators (Google, Outlook, Apple, …) all emit DTSTAMP on
    every iTIP message, so this fallback only fires for minimal /
    hand-crafted invites. When it does fire and we synthesize
    ``now``, a later legitimate UPDATE that carries an *older*
    DTSTAMP (because the organizer authored it before our store
    observed the original) can be misread as outdated and dropped
    — the user appears to be on a stale copy of the event. The
    LAST-MODIFIED → CREATED preference makes this less likely by
    preserving the authoring order when those are present; ``now``
    is a true last-resort that we accept can cause sequence
    inversion on follow-up updates.
    """
    fresh = ICalEvent()
    recurrence_dropped = False
    for key in list(src.keys()):
        upper = key.upper()
        if upper not in _VEVENT_KEEP:
            continue
        value = src[key]
        for item in value if isinstance(value, list) else [value]:
            if upper in _SINGLE_DATES and upper in fresh:
                # RFC 5545 allows one: a second would make readers fail.
                continue
            cleaned = _clean_property_value(upper, item)
            if cleaned is None:
                recurrence_dropped |= upper in _RECURRENCE_DATES
                continue
            _filter_params(upper, cleaned)
            # ``encode=0``: the value is already a typed icalendar
            # object (vText / vDDDTypes / vCalAddress / vRecur); we
            # don't want add() to re-encode and lose the params.
            fresh.add(key, cleaned, encode=0)

    # Without a readable DTSTART the event cannot be shown or scheduled. A
    # recurrence date that cannot be read is not dropped alone: without its
    # RECURRENCE-ID an exception would become a second master of the
    # series, and without an RDATE or EXDATE the series would miss an
    # occurrence or show a cancelled one.
    if "UID" not in fresh or "DTSTART" not in fresh or recurrence_dropped:
        return None
    rules = fresh.get("RRULE")
    if rules is not None:
        rules = rules if isinstance(rules, list) else [rules]
        kept = [r for r in rules if _subhourly_until_is_small(r, fresh["DTSTART"].dt)]
        if len(kept) != len(rules):
            del fresh["RRULE"]
            for rule in kept:
                fresh.add("RRULE", rule, encode=0)
    stamp = fresh.get("DTSTAMP")
    if stamp is not None and not isinstance(stamp.dt, datetime):
        # RFC 5545 §3.8.7.2: a DTSTAMP is a UTC date-time.
        del fresh["DTSTAMP"]
    if "DTSTAMP" not in fresh:
        # Prefer iTIP versioning info already present on the event
        # (LAST-MODIFIED → CREATED) over server-now. Setting DTSTAMP
        # to "now" effectively claims this event was authored this
        # second, which breaks iTIP sequencing if the organizer ever
        # sends an update; the original timestamps preserve order.
        fresh.add(
            "DTSTAMP",
            _utc_stamp(fresh.get("LAST-MODIFIED"))
            or _utc_stamp(fresh.get("CREATED"))
            or datetime.now(tz=timezone.utc),
        )
    return fresh


def _utc_stamp(prop):
    """``prop``'s date-time in UTC, or None if absent or only a date."""
    value = getattr(prop, "dt", None)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _readable_dates(prop, value):
    """Whether every date of ``value`` can be read.

    icalendar raises on access for an unparseable value (``garbage``, year
    0). A single date property must hold one date or date-time; RDATE and
    EXDATE hold a list, where RDATE may also hold periods.
    """
    try:
        if prop in _SINGLE_DATES:
            return isinstance(value.dt, date)
        return all(
            isinstance(item.dt, (date, tuple)) for item in getattr(value, "dts", [])
        )
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _clean_property_value(prop, value):
    """Per-property validation. Returns ``None`` to drop the value."""
    if prop == "URL":
        if not _SAFE_URL_RE.match(str(value)):
            return None
    elif prop in _SINGLE_DATES or prop in ("RDATE", "EXDATE"):
        if not _readable_dates(prop, value):
            return None
    elif prop == "RRULE":
        # icalendar represents RRULE as a vRecur (dict-like) where
        # each entry is a list (``{"FREQ": ["SECONDLY"]}``). A broken
        # rule raises on access.
        try:
            freq = (value.get("FREQ") or [""])[0]
            count = (value.get("COUNT") or [None])[0]
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if str(freq).upper() in _RRULE_FREQ_REQUIRES_BOUND:
            # A small COUNT keeps the expansion small. An UNTIL alone (it can
            # be year 9999) is checked against DTSTART in ``_rebuild_event``.
            if count is None and value.get("UNTIL"):
                return value
            if not isinstance(count, int) or not 0 < count <= _SUBHOURLY_MAX_COUNT:
                return None
    return value


def _subhourly_until_is_small(rule, dtstart):
    """Whether a sub-hourly ``rule`` bounded by UNTIL alone has at most
    ``_SUBHOURLY_MAX_COUNT`` occurrences from ``dtstart``. True for any other
    rule (already checked by ``_clean_property_value``).

    Counted, not expanded: expanding a rule whose BY* parts rarely match
    walks every second or minute until UNTIL.
    """
    try:
        freq = str((rule.get("FREQ") or [""])[0]).upper()
        if freq not in _RRULE_FREQ_REQUIRES_BOUND or rule.get("COUNT"):
            return True
        interval = (rule.get("INTERVAL") or [1])[0]
        until = rule["UNTIL"][0]
        if not isinstance(interval, int) or interval < 1:
            return False
        start = (
            dtstart
            if isinstance(dtstart, datetime)
            else datetime.combine(dtstart, dt_time.min)
        )
        if not isinstance(until, datetime):
            until = datetime.combine(until, dt_time.max)
        span = (until - start).total_seconds()
    except Exception:  # pylint: disable=broad-exception-caught
        # Unreadable, or a naive UNTIL with an aware DTSTART (or the reverse).
        return False
    step = interval * (60 if freq == "MINUTELY" else 1)
    steps = max(span, 0) // step + 1
    # MINUTELY expands BYSECOND (RFC 5545 §3.3.10); the other BY* only limit.
    per_step = len(rule.get("BYSECOND") or [0]) if freq == "MINUTELY" else 1
    return steps * per_step <= _SUBHOURLY_MAX_COUNT


def _filter_params(prop, value):
    """Drop X-* / unknown params from a property value, in place.

    Properties not listed in ``_PARAM_KEEP`` get all their parameters
    stripped — default-deny matches the rebuild posture.
    """
    params = getattr(value, "params", None)
    if not params:
        return
    allowed = _PARAM_KEEP.get(prop, frozenset())
    for k in list(params.keys()):
        if k.upper() not in allowed:
            del params[k]

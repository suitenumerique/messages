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
from datetime import datetime, timezone

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
# COUNT/UNTIL (Mozilla bug 1770984). Reject these unless explicitly
# bounded.
_RRULE_FREQ_REQUIRES_BOUND = frozenset({"SECONDLY", "MINUTELY"})


def rebuild_for_storage(cal):
    """Return a fresh VCALENDAR containing only allowlisted properties.

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
            continue
        for prop in ("DTSTART", "DTEND", "RECURRENCE-ID", "RDATE", "EXDATE"):
            val = clean.get(prop)
            if val is None:
                continue
            for v in val if isinstance(val, list) else [val]:
                tzid = getattr(v, "params", {}).get("TZID")
                if tzid:
                    referenced_tzids.add(str(tzid))
        rebuilt_events.append(clean)

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
    RFC 5545 — skip rather than store). A missing DTSTAMP is
    synthesized (see fallback chain below).

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
    for key in list(src.keys()):
        upper = key.upper()
        if upper not in _VEVENT_KEEP:
            continue
        value = src[key]
        for item in value if isinstance(value, list) else [value]:
            cleaned = _clean_property_value(upper, item)
            if cleaned is None:
                continue
            _filter_params(upper, cleaned)
            # ``encode=0``: the value is already a typed icalendar
            # object (vText / vDDDTypes / vCalAddress / vRecur); we
            # don't want add() to re-encode and lose the params.
            fresh.add(key, cleaned, encode=0)

    if "UID" not in fresh:
        return None
    if "DTSTAMP" not in fresh:
        # Prefer iTIP versioning info already present on the event
        # (LAST-MODIFIED → CREATED) over server-now. Setting DTSTAMP
        # to "now" effectively claims this event was authored this
        # second, which breaks iTIP sequencing if the organizer ever
        # sends an update; the original timestamps preserve order.
        fallback = fresh.get("LAST-MODIFIED") or fresh.get("CREATED")
        if fallback is not None and hasattr(fallback, "dt"):
            fresh.add("DTSTAMP", fallback.dt)
        else:
            fresh.add("DTSTAMP", datetime.now(tz=timezone.utc))
    return fresh


def _clean_property_value(prop, value):
    """Per-property validation. Returns ``None`` to drop the value."""
    if prop == "URL":
        if not _SAFE_URL_RE.match(str(value)):
            return None
    elif prop == "RRULE":
        # icalendar represents RRULE as a vRecur (dict-like) where
        # each entry is a list (``{"FREQ": ["SECONDLY"]}``).
        try:
            freq = (value.get("FREQ") or [""])[0]
        except (AttributeError, TypeError):
            return None
        if str(freq).upper() in _RRULE_FREQ_REQUIRES_BOUND:
            if not (value.get("COUNT") or value.get("UNTIL")):
                return None
    return value


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

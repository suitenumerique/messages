"""Read the ATTENDEE properties of an iCalendar event."""

from core.mda.addresses import ascii_lower, normalize_address


def attendee_key(value) -> str:
    """Canonical identity for an ICS ATTENDEE or a calendar owner address.

    Drops the ``mailto:`` URI prefix, ASCII-folds the local part and
    canonicalizes the domain, so one identity written as a U-label and as its
    A-label compares equal. Used for every ATTENDEE match, so both sides of a
    comparison are folded the same way rather than each site rolling its own.
    """
    address = str(value or "").strip()
    # ASCII fold for the scheme test too: a URI scheme is ASCII by definition
    # (RFC 3986 §3.1), so nothing outside it should be able to spell one.
    if ascii_lower(address).startswith("mailto:"):
        address = address[len("mailto:") :]
    return normalize_address(address)


def attendees_of(event, attendee_email):
    """Return the ATTENDEE properties of one VEVENT for this address."""
    target = attendee_key(attendee_email)
    attendees = event.get("ATTENDEE") or []
    if not isinstance(attendees, list):
        attendees = [attendees]
    return [att for att in attendees if attendee_key(att) == target]


def partstat_of(event, attendee_email):
    """Return the PARTSTAT of the attendee on one VEVENT.

    An attendee without PARTSTAT has NEEDS-ACTION, as in RFC 5545. Returns
    None when the attendee is not on the VEVENT.
    """
    attendees = attendees_of(event, attendee_email)
    if not attendees:
        return None
    return str(attendees[0].params.get("PARTSTAT") or "NEEDS-ACTION").upper()

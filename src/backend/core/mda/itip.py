"""Inbound iTIP (RFC 5546) ``METHOD:REPLY`` detection and trust gating."""

import logging

from icalendar import Calendar as ICalendar
from jmap_email import body_part_text, first_address_email

logger = logging.getLogger(__name__)

_VERDICT_UNVERIFIED = "none"
_VERDICT_FORGED = "fail"

FLAG_VERIFIED = "verified"
FLAG_UNVERIFIED = "unverified"

_CALENDAR_TYPE = "text/calendar"

# Cap a calendar part before it rides through the Celery broker or the parser.
# A legitimate single-event REPLY is a few KB; this is a generous abuse bound.
_MAX_REPLY_ICS_CHARS = 512 * 1024


def _iter_calendar_parts(parsed_email):
    """Yield every ``text/calendar`` EmailBodyPart in the message tree."""
    root = parsed_email.get("bodyStructure")
    if root:
        stack = [root]
        while stack:
            part = stack.pop()
            if not isinstance(part, dict):
                continue
            sub = part.get("subParts")
            if sub:
                stack.extend(sub)
                continue
            if (part.get("type") or "").lower() == _CALENDAR_TYPE:
                yield part
        return
    for key in ("attachments", "textBody"):
        for part in parsed_email.get(key) or []:
            if isinstance(part, dict) and (part.get("type") or "").lower() == (
                _CALENDAR_TYPE
            ):
                yield part


def _part_ics_text(parsed_email, part):
    """Decode a calendar part to text across both parser projections."""
    text = body_part_text(parsed_email, part)
    if text:
        return text
    raw = part.get("content")
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return ""


def find_reply_ics(parsed_email):
    """Return the ICS text of the first ``METHOD:REPLY`` calendar part, or None."""
    for part in _iter_calendar_parts(parsed_email):
        ics = _part_ics_text(parsed_email, part)
        if not ics:
            continue
        if len(ics) > _MAX_REPLY_ICS_CHARS:
            logger.info("Skipping oversized text/calendar part (%d chars)", len(ics))
            continue
        try:
            cal = ICalendar.from_ical(ics)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Skipping unparseable text/calendar part", exc_info=True)
            continue
        if str(cal.get("METHOD") or "").upper() == "REPLY":
            return ics
    return None


def reply_partstat(ics_text, addr):
    """The PARTSTAT the ATTENDEE matching ``addr`` gives itself, or None.

    Never returns another attendee's PARTSTAT — a REPLY may list several.
    """
    target = (addr or "").strip().lower()
    if not target:
        return None
    try:
        cal = ICalendar.from_ical(ics_text)
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    for comp in cal.walk("VEVENT"):
        attendees = comp.get("ATTENDEE")
        if attendees is None:
            continue
        if not isinstance(attendees, list):
            attendees = [attendees]
        matched = None
        for att in attendees:
            a = str(att).strip().lower()
            if a.startswith("mailto:"):
                a = a[len("mailto:") :]
            if a == target:
                ps = att.params.get("PARTSTAT")
                matched = str(ps) if ps else None
                break  # first address match wins, mirroring _parse_reply
        if matched:
            return matched
    return None


def reply_is_recurrence_instance(ics_text):
    """True if any VEVENT carries a RECURRENCE-ID (single-instance reply)."""
    try:
        cal = ICalendar.from_ical(ics_text)
    except Exception:  # pylint: disable=broad-exception-caught
        return False
    return any(c.get("RECURRENCE-ID") is not None for c in cal.walk("VEVENT"))


def decide(auth_verdict, apply_unverified):
    """Trust decision from the sender-auth verdict → ``(should_apply, flag)``.

    ``fail`` never applies; ``none`` applies only with ``apply_unverified``
    (flagged unverified); absent verdict = verified.
    """
    if auth_verdict == _VERDICT_FORGED:
        return False, None
    if auth_verdict == _VERDICT_UNVERIFIED:
        if not apply_unverified:
            return False, None
        return True, FLAG_UNVERIFIED
    return True, FLAG_VERIFIED


def evaluate_inbound_reply(parsed_email, auth_verdict, apply_unverified):
    """Detect + gate an inbound REPLY → ``(ics, flag, attendee)`` or all-None.

    ``attendee`` is the DMARC-verified From — the only identity the apply path
    may act on. Requiring it to be an ATTENDEE with a PARTSTAT blocks a crafted
    reply whose aligned From is PARTSTAT-less while another ATTENDEE carries one.
    """
    ics = find_reply_ics(parsed_email)
    if ics is None:
        return None, None, None
    from_addr = (first_address_email(parsed_email.get("from")) or "").strip().lower()
    if not from_addr:
        return None, None, None
    if reply_partstat(ics, from_addr) is None:
        logger.info(
            "iTIP REPLY From %r is not an RSVPing attendee; skipping", from_addr
        )
        return None, None, None
    # Reject recurrence-instance replies at the gate (no flag, no fan-out); the
    # service keeps its own check as defense in depth.
    if reply_is_recurrence_instance(ics):
        logger.info("iTIP REPLY targets a recurrence instance; skipping")
        return None, None, None
    should_apply, flag = decide(auth_verdict, apply_unverified)
    if not should_apply:
        return None, None, None
    return ics, flag, from_addr

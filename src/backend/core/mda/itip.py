"""Inbound iTIP (RFC 5546) ``METHOD:REPLY`` detection and trust gating."""

import logging
from enum import StrEnum

from jmap_email import body_part_text

from core.services.calendar.attendees import attendee_key
from core.services.calendar.untrusted_ics import parse_untrusted_ics

logger = logging.getLogger(__name__)


class _AuthVerdict(StrEnum):
    """Sender-auth verdict of an inbound message. No verdict means verified."""

    UNVERIFIED = "none"
    FORGED = "fail"


class ItipReplyFlag(StrEnum):
    """Trust level of an inbound REPLY. The postmark keeps it as ``itip-reply``."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


_CALENDAR_TYPE = "text/calendar"

# Cap a calendar part before it rides through the Celery broker or the parser.
# A legitimate single-event REPLY is a few KB; this is a generous abuse bound.
_MAX_REPLY_ICS_CHARS = 512 * 1024


def _iter_calendar_parts(parsed_email):
    """Yield every ``text/calendar`` EmailBodyPart in the message tree."""
    root = parsed_email.get("bodyStructure")
    if not root:
        for key in ("attachments", "textBody"):
            for part in parsed_email.get(key) or []:
                if isinstance(part, dict) and (part.get("type") or "").lower() == (
                    _CALENDAR_TYPE
                ):
                    yield part
        return
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
            cal = parse_untrusted_ics(ics)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Skipping unparseable text/calendar part", exc_info=True)
            continue
        if str(cal.get("METHOD") or "").upper() == "REPLY":
            return ics
    return None


def reply_partstat(ics_text, addr):
    """Return the PARTSTAT of the ATTENDEE ``addr``, or None.

    A REPLY can list more than one ATTENDEE. The PARTSTAT of another
    attendee is never returned.
    """
    target = attendee_key(addr)
    if not target:
        return None
    try:
        cal = parse_untrusted_ics(ics_text)
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
            if attendee_key(att) == target:
                ps = att.params.get("PARTSTAT")
                matched = str(ps) if ps else None
                break  # first address match wins, mirroring _parse_reply
        if matched:
            return matched
    return None


def decide(auth_verdict, apply_unverified):
    """Return ``(should_apply, flag)`` for the sender-auth verdict.

    A ``fail`` verdict never applies. A ``none`` verdict applies only with
    ``apply_unverified``, with the flag ``unverified``. No verdict means a
    verified sender.
    """
    if auth_verdict == _AuthVerdict.FORGED:
        return False, None
    if auth_verdict == _AuthVerdict.UNVERIFIED:
        if not apply_unverified:
            return False, None
        return True, ItipReplyFlag.UNVERIFIED
    return True, ItipReplyFlag.VERIFIED


def evaluate_inbound_reply(parsed_email, auth_verdict, apply_unverified):
    """Find and check an inbound REPLY.

    Returns ``(ics, flag, attendee)``, or three None when the REPLY does not
    apply. ``attendee`` is the authenticated From address. It is the only
    identity that the apply path can change. The From address must be an
    ATTENDEE with a PARTSTAT. This stops a crafted reply where the From
    ATTENDEE has no PARTSTAT and another ATTENDEE has one.
    """
    should_apply, flag = decide(auth_verdict, apply_unverified)
    if not should_apply:
        return None, None, None
    # Not ``str.lower()``: it maps some non-ASCII letters to ASCII letters, and
    # the sender would then match the address of another attendee.
    senders = {
        attendee_key(address.get("email")) for address in parsed_email.get("from") or []
    }
    # ``alice@a.example <bob@b.example>`` gives two addresses. The checks of
    # the sender can read Bob, and Alice must not get the answer of Bob.
    if len(senders) != 1:
        logger.info("iTIP REPLY From has %d addresses; skipping", len(senders))
        return None, None, None
    (from_addr,) = senders
    if not from_addr:
        return None, None, None
    ics = find_reply_ics(parsed_email)
    if ics is None:
        return None, None, None
    if reply_partstat(ics, from_addr) is None:
        logger.info("iTIP REPLY From is not an RSVPing attendee; skipping")
        return None, None, None
    return ics, flag, from_addr

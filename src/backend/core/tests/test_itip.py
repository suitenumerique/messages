"""Unit tests for inbound iTIP REPLY detection + trust gating (core.mda.itip)."""
# pylint: disable=missing-function-docstring,missing-class-docstring

from core.mda import itip

REPLY_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
METHOD:REPLY
BEGIN:VEVENT
UID:evt-1@example.com
DTSTAMP:20260101T120000Z
DTSTART:20260601T100000Z
DTEND:20260601T110000Z
SEQUENCE:0
ORGANIZER:mailto:org@example.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:Alice@Corp.example
END:VEVENT
END:VCALENDAR"""

REQUEST_ICS = REPLY_ICS.replace("METHOD:REPLY", "METHOD:REQUEST")

# Crafted attack: the aligned From (attacker) is a PARTSTAT-less ATTENDEE, while
# a *different* ATTENDEE (victim) carries the PARTSTAT. Must never move victim.
TWO_ATTENDEE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
METHOD:REPLY
BEGIN:VEVENT
UID:evt-1@example.com
DTSTAMP:20260101T120000Z
DTSTART:20260601T100000Z
DTEND:20260601T110000Z
SEQUENCE:0
ORGANIZER:mailto:org@example.com
ATTENDEE:mailto:attacker@corp.example
ATTENDEE;PARTSTAT=DECLINED:mailto:victim@corp.example
END:VEVENT
END:VCALENDAR"""


def _parsed(ics, from_email):
    """Minimal JmapEmail-shaped dict with a single text/calendar leaf."""
    return {
        "from": [{"email": from_email}],
        "bodyStructure": {
            "type": "text/calendar",
            "content": ics,
            "subParts": None,
        },
    }


class TestDecide:
    def test_verified_applies_verified(self):
        assert itip.decide(None, False) == (True, itip.FLAG_VERIFIED)

    def test_forged_never_applies(self):
        assert itip.decide("fail", False) == (False, None)
        assert itip.decide("fail", True) == (False, None)

    def test_unverified_skipped_by_default(self):
        assert itip.decide("none", False) == (False, None)

    def test_unverified_applied_when_opted_in(self):
        assert itip.decide("none", True) == (True, itip.FLAG_UNVERIFIED)


class TestReplyPartstat:
    def test_returns_own_partstat(self):
        assert itip.reply_partstat(REPLY_ICS, "alice@corp.example") == "ACCEPTED"

    def test_case_insensitive(self):
        assert itip.reply_partstat(REPLY_ICS, "Alice@Corp.Example") == "ACCEPTED"

    def test_none_for_partstatless_attendee(self):
        # attacker is an ATTENDEE but carries no PARTSTAT.
        assert itip.reply_partstat(TWO_ATTENDEE_ICS, "attacker@corp.example") is None

    def test_never_returns_another_attendees_partstat(self):
        # asking for the victim returns victim's own; asking for attacker never
        # leaks victim's DECLINED.
        assert itip.reply_partstat(TWO_ATTENDEE_ICS, "victim@corp.example") == (
            "DECLINED"
        )
        assert itip.reply_partstat(TWO_ATTENDEE_ICS, "attacker@corp.example") is None

    def test_none_when_unparseable(self):
        assert itip.reply_partstat("not an ics", "a@b.example") is None


class TestFindReplyIcs:
    def test_finds_reply_part(self):
        assert itip.find_reply_ics(_parsed(REPLY_ICS, "alice@corp.example")) is not None

    def test_ignores_request_method(self):
        assert itip.find_reply_ics(_parsed(REQUEST_ICS, "org@example.com")) is None

    def test_no_calendar_part(self):
        parsed = {
            "from": [{"email": "a@b.example"}],
            "bodyStructure": {"type": "text/plain", "content": "hi", "subParts": None},
        }
        assert itip.find_reply_ics(parsed) is None

    def test_finds_in_attachments_fallback(self):
        parsed = {
            "from": [{"email": "alice@corp.example"}],
            "attachments": [{"type": "text/calendar", "content": REPLY_ICS}],
        }
        assert itip.find_reply_ics(parsed) is not None


class TestEvaluateInboundReply:
    def test_verified_sender_rsvping(self):
        parsed = _parsed(REPLY_ICS, "alice@corp.example")
        ics, flag, attendee = itip.evaluate_inbound_reply(parsed, None, False)
        assert ics is not None
        assert flag == itip.FLAG_VERIFIED
        assert attendee == "alice@corp.example"

    def test_from_not_an_rsvping_attendee_rejected(self):
        # From is the organizer, not an ATTENDEE carrying a PARTSTAT.
        parsed = _parsed(REPLY_ICS, "org@example.com")
        assert itip.evaluate_inbound_reply(parsed, None, False) == (None, None, None)

    def test_crafted_two_attendee_payload_rejected(self):
        # From=attacker (aligned, DMARC-pass) but attacker has no PARTSTAT; the
        # PARTSTAT belongs to victim. Gate must refuse — attacker isn't RSVPing.
        parsed = _parsed(TWO_ATTENDEE_ICS, "attacker@corp.example")
        assert itip.evaluate_inbound_reply(parsed, None, False) == (None, None, None)

    def test_forged_rejected(self):
        parsed = _parsed(REPLY_ICS, "alice@corp.example")
        assert itip.evaluate_inbound_reply(parsed, "fail", True) == (None, None, None)

    def test_unverified_skipped_by_default(self):
        parsed = _parsed(REPLY_ICS, "alice@corp.example")
        assert itip.evaluate_inbound_reply(parsed, "none", False) == (None, None, None)

    def test_unverified_applied_when_opted_in(self):
        parsed = _parsed(REPLY_ICS, "alice@corp.example")
        ics, flag, attendee = itip.evaluate_inbound_reply(parsed, "none", True)
        assert ics is not None
        assert flag == itip.FLAG_UNVERIFIED
        assert attendee == "alice@corp.example"

    def test_no_reply_part(self):
        parsed = _parsed(REQUEST_ICS, "org@example.com")
        assert itip.evaluate_inbound_reply(parsed, None, False) == (None, None, None)

    def test_recurrence_instance_rejected_at_gate(self):
        ics = REPLY_ICS.replace(
            "SEQUENCE:0", "SEQUENCE:0\nRECURRENCE-ID:20260601T100000Z"
        )
        parsed = _parsed(ics, "alice@corp.example")
        assert itip.evaluate_inbound_reply(parsed, None, False) == (None, None, None)


class TestRecurrenceDetection:
    def test_detects_recurrence_id(self):
        ics = REPLY_ICS.replace(
            "SEQUENCE:0", "SEQUENCE:0\nRECURRENCE-ID:20260601T100000Z"
        )
        assert itip.reply_is_recurrence_instance(ics) is True

    def test_plain_reply_is_not_recurrence(self):
        assert itip.reply_is_recurrence_instance(REPLY_ICS) is False

    def test_unparseable_is_not_recurrence(self):
        assert itip.reply_is_recurrence_instance("nope") is False

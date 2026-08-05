"""
Fuzzing the parse/compose seam.

The suites next door fuzz each direction alone: ``test_message_fuzz``
feeds junk to the parser, ``test_composer_fuzz`` checks the composer's
output is wire-legal. Neither exercises the *seam* — and the seam is
where a mail system lives, because every reply, forward and autoreply is
a parse followed by a compose of something an attacker wrote.

The properties here are about containment across that round trip: no
value carried through may turn into a header, a recipient, or a MIME
part that the input did not already contain.

Run with: pytest -m fuzz tests/test_roundtrip_fuzz.py
Or: make fuzz-jmap-email
"""

import email
import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from jmap_email import ComposeError, compose_email, is_valid_addr_spec, parse_email

FUZZ_SETTINGS = {
    # Override for a deeper soak: FUZZ_EXAMPLES=100000 make fuzz-jmap-email
    "max_examples": int(os.environ.get("FUZZ_EXAMPLES", "10000")),
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    # Phases are Hypothesis's defaults on purpose. ``shrink`` and
    # ``explain`` cost nothing on a green run — they only engage once a
    # failure exists, which is exactly when you want a minimal example
    # rather than the raw generated blob. ``reuse`` replays a stored
    # failure until it is fixed, which is what makes an intermittent
    # find reproducible; it needs ``.hypothesis`` to survive the
    # container, so compose mounts it.
}

# Text biased toward characters that mean something structural in a
# header: the separators, the delimiters, the line terminators, and the
# encoded-word syntax that smuggles all of them past a naive check.
_hostile_text = st.one_of(
    st.text(max_size=40),
    st.sampled_from(
        [
            "\r\n",
            "\n",
            "\r",
            "\r\n ",
            "\r\nBcc: evil@x.co",
            "\r\n\r\n",
            ",",
            ", evil@x.co",
            ";",
            "<",
            ">",
            '"',
            "\\",
            "@",
            ":",
            "\x00",
            "\x1b",
            "",
            " ",
            "=?utf-8?B?ZXZpbEB4LmNv?=",
            "=?utf-8?q?a=0d=0aBcc:_e@x.co?=",
            "--boundary",
            "\r\n--boundary",
            "Content-Type: text/html",
            "é",
            " ",
        ]
    ),
)

_addr = st.builds(
    lambda a, b: f"{a}@{b}",
    st.text(max_size=12),
    st.text(max_size=12),
) | st.sampled_from(
    [
        "a@b.co",
        "a@b.co, evil@x.co",
        "a b@c.co",
        "@b.co",
        "a@",
        '"a b"@c.co',
        "é@ü.co",
    ]
)

_mailbox = st.builds(
    lambda n, e: {"name": n, "email": e}, st.one_of(st.none(), _hostile_text), _addr
)

jmap_email_dict = st.builds(
    lambda frm, to, cc, subject, body: {
        "from": [frm],
        "to": to,
        "cc": cc,
        "subject": subject,
        "sentAt": "2026-06-08T12:00:00+00:00",
        "textBody": [{"partId": "1", "type": "text/plain", "content": body}],
    },
    _mailbox,
    st.lists(_mailbox, max_size=3),
    st.lists(_mailbox, max_size=2),
    _hostile_text,
    _hostile_text,
)


def _compose(jmap):
    try:
        return compose_email(jmap)
    except ComposeError:
        return None


def _separator_commas(value: str) -> int:
    """Count commas that actually separate mailboxes.

    A comma inside a quoted display name is data, not a separator.
    """
    count = 0
    in_quotes = False
    escaped = False
    for ch in value:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            count += 1
    return count


@pytest.mark.fuzz
class TestRoundTripContainment:
    """Nothing may gain structure by passing through compose then parse."""

    @settings(**FUZZ_SETTINGS)
    @given(jmap=jmap_email_dict)
    def test_recipient_count_never_grows(self, jmap):
        """The wire must not carry more mailboxes than we were given.

        A single ``email`` containing a comma used to become two
        recipients: the mailbox-list is built by joining on commas, so a
        value carrying one is address injection performed by the library
        on its caller's behalf.
        """
        raw = _compose(jmap)
        if raw is None:
            return
        parsed = email.message_from_bytes(raw)
        for jmap_key, header in (("to", "To"), ("cc", "Cc")):
            value = parsed[header]
            if not value:
                continue
            supplied = sum(1 for m in jmap[jmap_key] if (m.get("email") or "").strip())
            assert _separator_commas(str(value)) + 1 <= supplied

    @settings(**FUZZ_SETTINGS)
    @given(jmap=jmap_email_dict)
    def test_no_header_is_smuggled(self, jmap):
        """Composed output must not carry a header we never set."""
        raw = _compose(jmap)
        if raw is None:
            return
        parsed = email.message_from_bytes(raw)
        allowed = {
            "from",
            "to",
            "cc",
            "bcc",
            "reply-to",
            "sender",
            "subject",
            "date",
            "message-id",
            "in-reply-to",
            "references",
            "mime-version",
            "content-type",
            "content-transfer-encoding",
            "content-disposition",
            "content-id",
        }
        for name in parsed.keys():
            assert str(name).lower() in allowed, f"smuggled header {name!r}"

    @settings(**FUZZ_SETTINGS)
    @given(jmap=jmap_email_dict)
    def test_body_cannot_forge_a_mime_part(self, jmap):
        """A body carrying ``--boundary`` must not split the message.

        This is what an unpredictable boundary buys: the part count is a
        function of what we built, never of what the body said.
        """
        raw = _compose(jmap)
        if raw is None:
            return
        parsed = email.message_from_bytes(raw)
        if parsed.is_multipart():
            assert len(parsed.get_payload()) <= 2

    @settings(**FUZZ_SETTINGS)
    @given(jmap=jmap_email_dict)
    def test_no_address_appears_that_we_never_supplied(self, jmap):
        """Recovery is best-effort on exotic input; invention is not."""
        raw = _compose(jmap)
        if raw is None:
            return
        reparsed = parse_email(raw)
        assert reparsed is not None
        supplied = {
            (m.get("email") or "").strip()
            for m in jmap["to"]
            if is_valid_addr_spec((m.get("email") or "").strip())
        }
        # A display name is not an address, however much it looks like
        # one — this is the property that caught the composer
        # RFC 2047-encoding addr-specs, which made a lenient re-parse
        # fall back to the decoded display name as the recipient.
        recovered = {a["email"] for a in (reparsed.get("to") or [])}
        assert recovered <= supplied or not supplied

    @settings(**FUZZ_SETTINGS)
    @given(jmap=jmap_email_dict)
    def test_boundary_is_fresh_every_compose(self, jmap):
        """Two composes agree on the headers and differ on the boundary —
        if they matched, it would be predictable."""
        a, b = _compose(jmap), _compose(jmap)
        if a is None or b is None:
            assert a is None and b is None
            return
        pa, pb = email.message_from_bytes(a), email.message_from_bytes(b)
        assert pa["Subject"] == pb["Subject"]
        assert pa["To"] == pb["To"]
        if pa.is_multipart():
            assert pa.get_boundary() != pb.get_boundary()

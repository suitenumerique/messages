"""
Fuzzing the wire round trip: raw bytes → parse → compose → parse.

``test_roundtrip_fuzz`` starts from synthetic JMAP dicts. This one starts
from *bytes an attacker wrote*, which is the path a mail system actually
walks: inbound MIME is parsed, the result is edited into a reply or a
forward, and the composer turns it back into bytes. Anything the parser
mis-reports becomes something the composer emits under our own
signature.

The properties are about fidelity and non-amplification across that
loop. Fidelity, because a forward that changes the recipient list is a
bug; non-amplification, because a message that grows an address, a
header or a part each time it is forwarded is a weapon.

Run with: pytest -m fuzz tests/test_wire_roundtrip_fuzz.py
Or: make fuzz-jmap-email
"""

import email
import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from jmap_email import ComposeError, compose_email, parse_email

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

# Header lines assembled from fragments that mean something structural.
# The point is to build messages a real MTA might hand us, not uniform
# noise: the interesting inputs are almost-valid.
_header_line = st.one_of(
    st.sampled_from(
        [
            b"From: a@b.co",
            b"From: alice@good.co",
            b"From: mallory@evil.co",
            b'From: "=?utf-8?B?ZXZpbEB4LmNv?=" <a@b.co>',
            b"From: =?utf-8?B?ZXZpbEB4LmNv?= <a@b.co>",
            b"To: b@c.co",
            b"To: b@c.co, d@e.co",
            b"To: <a@b.co, evil@x.co>",
            b'To: "a b"@c.co',
            b"Cc: e@f.co",
            b"Subject: hi",
            b"Subject: =?utf-8?B?w6l0w6k=?=",
            b"Subject: =?utf-8?q?a=0d=0aBcc:_e@x.co?=",
            b"Date: Mon, 8 Jun 2026 12:00:00 +0200",
            b"Date: not-a-date",
            b"Message-ID: <m@x.co>",
            b"Message-ID: <foo$@local@domain>",
            b"In-Reply-To: <p@x.co>",
            b"References: <p@x.co> <q@x.co>",
            b"MIME-Version: 1.0",
            b"Content-Transfer-Encoding: base64",
            b"Content-Transfer-Encoding: bas64",
            b"Reply-To: r@s.co",
            b"X-Custom: v",
        ]
    ),
)

_body_block = st.sampled_from(
    [
        b"plain body\n",
        b"--x\nContent-Type: text/plain\n\npart one\n--x--\n",
        b"preamble text\n--x\nContent-Type: text/plain\n\npart\n--x--\nepilogue\n",
        b"--x\nContent-Type: application/pdf\n"
        b'Content-Disposition: attachment; filename="r.pdf"\n\nQUFB\n--x--\n',
        b"--x\nContent-Type: application/pdf\n"
        b"Content-Disposition: attachment; "
        b"filename=\"safe.txt\"; filename*=UTF-8''evil.exe\n\nQUFB\n--x--\n",
        b"--x\nContent-Type: message/rfc822\n\nFrom: i@n.co\nSubject: in\n\nx\n--x--\n",
        b"U01VR0dMRUQ=\n",
        b"U01VR0!!dMRUQ=\n",
        b"",
    ]
)

raw_message = st.builds(
    lambda headers, ct, body: b"\n".join(headers) + b"\n" + ct + b"\n\n" + body,
    st.lists(_header_line, min_size=1, max_size=7),
    st.sampled_from(
        [
            b"Content-Type: text/plain",
            b'Content-Type: multipart/mixed; boundary="x"',
            b'Content-Type: multipart/mixed; boundary="x"; boundary="y"',
            b"Content-Type: multipart/mixed",
            b"Content-Type: garbage",
        ]
    ),
    _body_block,
)


def _addrs(parsed, key):
    return {a["email"] for a in (parsed.get(key) or [])}


@pytest.mark.fuzz
class TestWireRoundTrip:
    """parse → compose → parse must not invent, and must not amplify."""

    @settings(**FUZZ_SETTINGS)
    @given(raw=raw_message)
    def test_parse_never_raises_on_wire_bytes(self, raw):
        """The documented contract: a single ``is None`` check, no except."""
        parsed = parse_email(raw, extensions=True)
        assert parsed is None or isinstance(parsed, dict)

    @settings(**FUZZ_SETTINGS)
    @given(raw=raw_message)
    def test_recompose_invents_no_address(self, raw):
        """Forwarding must not add a recipient.

        The composer signs what it emits with our identity, so an address
        that appears only after the round trip is one we vouched for and
        the sender never wrote.
        """
        first = parse_email(raw)
        if first is None:
            return
        try:
            rebuilt = compose_email(first)
        except ComposeError:
            return
        second = parse_email(rebuilt)
        assert second is not None
        for key in ("from", "to", "cc"):
            assert _addrs(second, key) <= _addrs(first, key)

    @settings(**FUZZ_SETTINGS)
    @given(raw=raw_message)
    def test_round_trip_reaches_a_fixed_point(self, raw):
        """A second lap must change nothing a third lap would change.

        Mail is forwarded repeatedly. A loop that keeps mutating the
        message — dropping a recipient each time, or re-encoding a
        subject — corrupts a thread by attrition rather than all at once.
        """
        first = parse_email(raw)
        if first is None:
            return
        try:
            once = compose_email(first)
        except ComposeError:
            return
        mid = parse_email(once)
        assert mid is not None
        try:
            twice = compose_email(mid)
        except ComposeError:
            pytest.fail("compose accepted its own output once but not twice")
        end = parse_email(twice)
        assert end is not None
        assert _addrs(end, "to") == _addrs(mid, "to")
        assert _addrs(end, "from") == _addrs(mid, "from")
        assert end["subject"] == mid["subject"]

    @settings(**FUZZ_SETTINGS)
    @given(raw=raw_message)
    def test_headers_do_not_multiply(self, raw):
        """No header may gain a copy by being round-tripped.

        A duplicated identity header is the spoof the parser flags on the
        way in; emitting one on the way out would manufacture it.
        """
        first = parse_email(raw)
        if first is None:
            return
        try:
            rebuilt = compose_email(first)
        except ComposeError:
            return
        emitted = email.message_from_bytes(rebuilt)
        names = [str(k).lower() for k in emitted.keys()]
        for singleton in ("from", "to", "cc", "subject", "date", "message-id"):
            assert names.count(singleton) <= 1, f"{singleton} emitted twice"

    @settings(**FUZZ_SETTINGS)
    @given(raw=raw_message)
    def test_attachments_do_not_multiply(self, raw):
        """Forwarding must not duplicate a payload."""
        first = parse_email(raw)
        if first is None:
            return
        try:
            rebuilt = compose_email(first)
        except ComposeError:
            return
        second = parse_email(rebuilt)
        assert second is not None
        assert len(second.get("attachments") or []) <= len(
            first.get("attachments") or []
        )

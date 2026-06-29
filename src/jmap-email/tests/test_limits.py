"""Tests for the per-call :class:`ParseLimits` context.

Pin the behavior that:
- Defaults reproduce the historical module-constant values.
- ``ParseLimits`` is frozen (a returned dict cannot be mutated by a
  caller and have that leak across other call sites).
- Custom ``limits=`` actually changes parser behavior — both wider
  (a parse that would have truncated now succeeds) and tighter
  (a parse that would have succeeded now truncates).
- Concurrent calls with different ``limits=`` do not interfere.
"""

import threading
from dataclasses import FrozenInstanceError

import pytest

from jmap_email import DEFAULT_PARSE_LIMITS, ParseLimits, parse_addresses, parse_email
from jmap_email.parser import (
    MAX_ADDRESS_LIST_BYTES,
    MAX_HEADER_VALUE_BYTES,
    MAX_MIME_NESTING_DEPTH,
    MAX_MIME_PARTS,
)


class TestParseLimitsShape:
    """The dataclass is the public contract."""

    def test_default_constructor_matches_module_constants(self):
        """``ParseLimits()`` reproduces the values exposed as
        ``MAX_*`` on :mod:`jmap_email.parser`."""
        defaults = ParseLimits()
        assert defaults.max_mime_nesting_depth == MAX_MIME_NESTING_DEPTH
        assert defaults.max_mime_parts == MAX_MIME_PARTS
        assert defaults.max_header_value_bytes == MAX_HEADER_VALUE_BYTES
        assert defaults.max_address_list_bytes == MAX_ADDRESS_LIST_BYTES

    def test_default_singleton_is_a_parse_limits_instance(self):
        assert isinstance(DEFAULT_PARSE_LIMITS, ParseLimits)

    def test_is_frozen(self):
        """A caller that holds a ``ParseLimits`` instance cannot mutate
        it after construction. Defends against an accidental
        ``limits.max_mime_parts = 5000`` leaking across other callers
        that share the same instance."""
        limits = ParseLimits()
        with pytest.raises(FrozenInstanceError):
            limits.max_mime_parts = 5000  # type: ignore[misc]

    def test_is_hashable(self):
        """Frozen + slots makes the instance hashable; callers can use
        it as a cache key (e.g. memoised ``parse_email``)."""
        assert hash(ParseLimits()) == hash(ParseLimits())
        assert hash(ParseLimits(max_mime_parts=5000)) != hash(ParseLimits())


class TestCustomLimitsOnParseEmail:
    """End-to-end: ``limits=`` changes what the parser tolerates."""

    @staticmethod
    def _flat_multipart(n: int) -> bytes:
        """Build a ``multipart/mixed`` with ``n`` text/plain leaves."""
        parts = []
        for i in range(n):
            parts.append(b"--B\r\nContent-Type: text/plain\r\n\r\nx%d\r\n" % i)
        return (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
            + b"".join(parts)
            + b"--B--\r\n"
        )

    def test_default_caps_truncate_at_max_mime_parts(self):
        """Above the default 1000-part cap, the body-structure walk
        truncates."""
        raw = self._flat_multipart(1500)
        parsed = parse_email(raw, body_structure=True)

        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        assert _count(parsed["bodyStructure"]) <= MAX_MIME_PARTS + 5

    def test_tighter_limits_truncate_earlier(self):
        """A 100-part cap truncates a 200-part input even though the
        default would have walked the whole tree."""
        raw = self._flat_multipart(200)
        tight = ParseLimits(max_mime_parts=100)
        parsed = parse_email(raw, body_structure=True, limits=tight)

        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        # Root + ~100 leaves + a few stubs.
        assert _count(parsed["bodyStructure"]) <= 110

    def test_wider_limits_accept_more_parts(self):
        """A 5000-part cap walks past the default 1000-part ceiling."""
        raw = self._flat_multipart(1500)
        wide = ParseLimits(max_mime_parts=5000)
        parsed = parse_email(raw, body_structure=True, limits=wide)

        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        # All 1500 leaves walked when the cap is well above the input
        # size; total is root + 1500.
        assert _count(parsed["bodyStructure"]) >= 1500

    def test_default_caps_truncate_header_value(self):
        """A header value beyond the default 100 KB cap gets truncated."""
        huge = b"x" * (MAX_HEADER_VALUE_BYTES + 1000)
        raw = b"From: a@b.c\r\nTo: d@e.f\r\nX-Big: " + huge + b"\r\n\r\nbody\r\n"
        parsed = parse_email(raw)
        xbig = next(h for h in parsed["headers"] if h["name"].lower() == "x-big")
        assert len(xbig["value"]) <= MAX_HEADER_VALUE_BYTES

    def test_tighter_header_cap_truncates_smaller(self):
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b"X-Med: " + (b"y" * 10000) + b"\r\n"
            b"\r\nbody\r\n"
        )
        tight = ParseLimits(max_header_value_bytes=500)
        parsed = parse_email(raw, limits=tight)
        xmed = next(h for h in parsed["headers"] if h["name"].lower() == "x-med")
        assert len(xmed["value"]) <= 500


class TestCustomLimitsOnParseAddresses:
    """``parse_addresses`` accepts the same ``limits=`` knob."""

    def test_default_caps_truncate_long_list(self):
        addresses = ", ".join(f"u{i}@example.com" for i in range(20_000))
        result = parse_addresses(addresses)
        # Truncation happens silently; final entry list may be capped.
        assert len(result) < 20_000

    def test_tighter_address_cap_yields_fewer_entries(self):
        addresses = ", ".join(f"u{i}@example.com" for i in range(1_000))
        tight = ParseLimits(max_address_list_bytes=200)
        result = parse_addresses(addresses, limits=tight)
        # 200 bytes of address-list text only fits a handful of entries.
        assert len(result) < 20


class TestNoCrossCallContamination:
    """Threads / sequential calls with different ``limits=`` must not
    leak state across each other — this is the entire reason the
    library exposes per-call limits rather than mutable module
    globals."""

    def test_sequential_calls_do_not_leak(self):
        raw_small = TestCustomLimitsOnParseEmail._flat_multipart(50)
        raw_big = TestCustomLimitsOnParseEmail._flat_multipart(1500)

        tight = ParseLimits(max_mime_parts=10)
        wide = ParseLimits(max_mime_parts=5000)

        # Tight then wide.
        a = parse_email(raw_small, body_structure=True, limits=tight)
        b = parse_email(raw_big, body_structure=True, limits=wide)

        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        assert _count(a["bodyStructure"]) <= 15
        assert _count(b["bodyStructure"]) >= 1500

        # Reverse order — wide then tight. The default singleton's
        # state never changes, so the second call still applies its
        # own cap.
        c = parse_email(raw_big, body_structure=True, limits=wide)
        d = parse_email(raw_small, body_structure=True, limits=tight)
        assert _count(c["bodyStructure"]) >= 1500
        assert _count(d["bodyStructure"]) <= 15

    def test_concurrent_calls_do_not_interfere(self):
        """Two threads parsing with different caps simultaneously must
        each see only their own cap.

        Pins the absence of a shared mutable cap variable.
        """
        raw_big = TestCustomLimitsOnParseEmail._flat_multipart(1500)

        tight = ParseLimits(max_mime_parts=10)
        wide = ParseLimits(max_mime_parts=5000)

        results: dict[str, int] = {}

        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        def _go(name: str, limits: ParseLimits) -> None:
            parsed = parse_email(raw_big, body_structure=True, limits=limits)
            results[name] = _count(parsed["bodyStructure"])

        threads = []
        for _ in range(4):
            t1 = threading.Thread(target=_go, args=("tight", tight))
            t2 = threading.Thread(target=_go, args=("wide", wide))
            threads.extend([t1, t2])
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Whichever thread wrote ``results["tight"]`` last still sees
        # the tight cap; same for wide.
        assert results["tight"] <= 15
        assert results["wide"] >= 1500


if __name__ == "__main__":
    pytest.main()

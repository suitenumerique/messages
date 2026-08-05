"""Tests for the per-call :class:`ParseOptions` context.

Pin the behavior that:
- The default caps are the documented values.
- ``ParseOptions`` is frozen (a returned dict cannot be mutated by a
  caller and have that leak across other call sites).
- Custom ``options=`` actually changes parser behavior — both wider
  (a parse that would have truncated now succeeds) and tighter
  (a parse that would have succeeded now truncates).
- Concurrent calls with different ``options=`` do not interfere.
"""

import threading
from dataclasses import FrozenInstanceError

import pytest

from jmap_email import DEFAULT_PARSE_OPTIONS, ParseOptions, parse_addresses, parse_email


class TestParseOptionsShape:
    """The dataclass is the public contract."""

    def test_default_caps_are_the_documented_values(self):
        """Literals, not a comparison against the same field: changing a
        default has to be a deliberate act that trips this."""
        defaults = ParseOptions()
        assert defaults.max_mime_nesting_depth == 100  # Postfix mime_nesting_limit
        assert defaults.max_mime_parts == 1000  # Go multipartmaxparts
        assert defaults.max_header_value_bytes == 102_400  # Postfix header_size_limit
        assert defaults.max_address_list_bytes == 100_000

    def test_default_preview_cap_is_the_rfc_ceiling(self):
        """``max_preview_chars`` defaults to 256, the RFC 8621 §4.1.4
        ceiling for ``preview`` (a hard MUST NOT)."""
        assert ParseOptions().max_preview_chars == 256

    def test_default_singleton_is_a_parse_options_instance(self):
        assert isinstance(DEFAULT_PARSE_OPTIONS, ParseOptions)

    def test_is_frozen(self):
        """A caller that holds a ``ParseOptions`` instance cannot mutate
        it after construction. Defends against an accidental
        ``options.max_mime_parts = 5000`` leaking across other callers
        that share the same instance."""
        options = ParseOptions()
        with pytest.raises(FrozenInstanceError):
            options.max_mime_parts = 5000  # type: ignore[misc]

    def test_is_hashable(self):
        """Frozen + slots makes the instance hashable; callers can use
        it as a cache key (e.g. memoised ``parse_email``)."""
        assert hash(ParseOptions()) == hash(ParseOptions())
        assert hash(ParseOptions(max_mime_parts=5000)) != hash(ParseOptions())


class TestCustomOptionsOnParseEmail:
    """End-to-end: ``options=`` changes what the parser tolerates."""

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

        assert _count(parsed["bodyStructure"]) <= (
            DEFAULT_PARSE_OPTIONS.max_mime_parts + 5
        )

    def test_tighter_limits_truncate_earlier(self):
        """A 100-part cap truncates a 200-part input even though the
        default would have walked the whole tree."""
        raw = self._flat_multipart(200)
        tight = ParseOptions(max_mime_parts=100)
        parsed = parse_email(raw, body_structure=True, options=tight)

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
        wide = ParseOptions(max_mime_parts=5000)
        parsed = parse_email(raw, body_structure=True, options=wide)

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

    def test_default_cap_rejects_an_over_long_header_value(self):
        """Rejected, not truncated: there is no safe cut point for an
        arbitrary field, and a shortened one still looks well-formed."""
        cap = DEFAULT_PARSE_OPTIONS.max_header_value_bytes
        huge = b"x" * (cap + 1000)
        raw = b"From: a@b.c\r\nTo: d@e.f\r\nX-Big: " + huge + b"\r\n\r\nbody\r\n"
        assert parse_email(raw) is None

    def test_value_at_the_cap_still_parses(self):
        """The cap is a ceiling, not an off-by-one rejection."""
        cap = DEFAULT_PARSE_OPTIONS.max_header_value_bytes
        raw = b"From: a@b.c\r\nX-Big: " + (b"x" * (cap - 10)) + b"\r\n\r\nbody\r\n"
        assert parse_email(raw) is not None

    def test_tighter_header_cap_rejects_smaller(self):
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b"X-Med: " + (b"y" * 10000) + b"\r\n"
            b"\r\nbody\r\n"
        )
        tight = ParseOptions(max_header_value_bytes=500)
        assert parse_email(raw, options=tight) is None
        assert parse_email(raw) is not None  # unchanged under the default


class TestPreviewCap:
    """``max_preview_chars`` bounds the server-set ``preview`` excerpt."""

    @staticmethod
    def _with_body(body: str) -> bytes:
        return (
            b"From: a@b.c\r\nTo: d@e.f\r\nContent-Type: text/plain\r\n\r\n"
            + body.encode()
        )

    # A run with no space at the 140/256 boundary, so truncation lands
    # mid-token and ``preview_text``'s trailing ``rstrip`` is a no-op —
    # keeps the length assertions exact.
    _LONG_BODY = "abcdefghij" * 100

    def test_default_caps_preview_at_256(self):
        """A 1000-char body yields a 256-char preview under the default
        cap."""
        parsed = parse_email(self._with_body(self._LONG_BODY))
        assert len(parsed["preview"]) == 256

    def test_tighter_cap_truncates_preview_earlier(self):
        """A 140-char cap (the Messages list-view snippet length)
        truncates a body the default would have kept to 256."""
        tight = ParseOptions(max_preview_chars=140)
        parsed = parse_email(self._with_body(self._LONG_BODY), options=tight)
        assert len(parsed["preview"]) == 140

    def test_short_body_is_unaffected_by_cap(self):
        """A body under the cap passes through whole (minus whitespace
        normalisation) regardless of the ceiling."""
        parsed = parse_email(self._with_body("hi there"), options=ParseOptions())
        assert parsed["preview"] == "hi there"

    def test_default_scan_cap_is_128kib(self):
        assert ParseOptions().max_preview_scan_bytes == 128 * 1024

    def test_scan_cap_bounds_the_preview_source(self):
        """Body text past ``max_preview_scan_bytes`` is not scanned, so it
        can't appear in the preview — the DoS bound, end-to-end."""
        body = "<span></span>" * 200 + "TAIL_TEXT"
        tight = ParseOptions(max_preview_scan_bytes=100)
        parsed = parse_email(self._with_body(body), options=tight)
        assert "TAIL_TEXT" not in parsed["preview"]


class TestCustomOptionsOnParseAddresses:
    """``parse_addresses`` accepts the same ``options=`` knob."""

    def test_default_caps_truncate_long_list(self):
        addresses = ", ".join(f"u{i}@example.com" for i in range(20_000))
        result = parse_addresses(addresses)
        # Truncation happens silently; final entry list may be capped.
        assert len(result) < 20_000

    def test_tighter_address_cap_yields_fewer_entries(self):
        addresses = ", ".join(f"u{i}@example.com" for i in range(1_000))
        tight = ParseOptions(max_address_list_bytes=200)
        result = parse_addresses(addresses, options=tight)
        # 200 bytes of address-list text only fits a handful of entries.
        assert len(result) < 20


class TestNoCrossCallContamination:
    """Threads / sequential calls with different ``options=`` must not
    leak state across each other — this is the entire reason the
    library exposes per-call options rather than mutable module
    globals."""

    def test_sequential_calls_do_not_leak(self):
        raw_small = TestCustomOptionsOnParseEmail._flat_multipart(50)
        raw_big = TestCustomOptionsOnParseEmail._flat_multipart(1500)

        tight = ParseOptions(max_mime_parts=10)
        wide = ParseOptions(max_mime_parts=5000)

        # Tight then wide.
        a = parse_email(raw_small, body_structure=True, options=tight)
        b = parse_email(raw_big, body_structure=True, options=wide)

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
        c = parse_email(raw_big, body_structure=True, options=wide)
        d = parse_email(raw_small, body_structure=True, options=tight)
        assert _count(c["bodyStructure"]) >= 1500
        assert _count(d["bodyStructure"]) <= 15

    def test_concurrent_calls_do_not_interfere(self):
        """Two threads parsing with different caps simultaneously must
        each see only their own cap.

        Pins the absence of a shared mutable cap variable.
        """
        raw_big = TestCustomOptionsOnParseEmail._flat_multipart(1500)

        tight = ParseOptions(max_mime_parts=10)
        wide = ParseOptions(max_mime_parts=5000)

        results: dict[str, int] = {}

        def _count(node):
            if node is None:
                return 0
            c = 1
            for sub in node.get("subParts") or []:
                c += _count(sub)
            return c

        def _go(name: str, options: ParseOptions) -> None:
            parsed = parse_email(raw_big, body_structure=True, options=options)
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

"""
Fuzzing tests for RFC5322 email address parsing.

These tests use hypothesis for property-based testing to find edge cases
and potential crashes in the email address parsing code.

Run with: pytest -m fuzz core/tests/mda/test_rfc5322_address_fuzz.py
Or: make fuzz-back
"""

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from core.mda.rfc5322.parser import (
    decode_email_header_text,
    parse_date,
    parse_email_address,
    parse_email_addresses,
)

# Intensive fuzzing settings
FUZZ_SETTINGS = {
    "max_examples": 10000,
    "deadline": None,  # No time limit per example
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    "phases": [Phase.generate, Phase.target],  # Skip shrinking for speed
}


# Custom strategies for email-like content
email_local_part = st.text(
    alphabet=st.sampled_from(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-"
    ),
    min_size=1,
    max_size=64,
)

email_domain = st.text(
    alphabet=st.sampled_from(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
    ),
    min_size=3,
    max_size=255,
)

# Strategy for valid-ish email addresses
email_address = st.builds(
    lambda local, domain: f"{local}@{domain}",
    email_local_part,
    email_domain,
)

# Strategy for display names (can include special chars)
display_name = st.text(min_size=0, max_size=100)

# Strategy for email with optional display name
email_with_name = st.one_of(
    email_address,
    st.builds(
        lambda name, email: f"{name} <{email}>",
        display_name,
        email_address,
    ),
    st.builds(
        lambda name, email: f'"{name}" <{email}>',
        display_name,
        email_address,
    ),
    st.builds(
        lambda name, email: f"'{name}' <{email}>",
        display_name,
        email_address,
    ),
)

# Strategy for group syntax patterns
group_syntax = st.one_of(
    st.just("undisclosed-recipients:;"),
    st.just("undisclosed-recipients:>"),
    st.builds(
        lambda name: f"{name}:;",
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz -", min_size=1, max_size=30),
    ),
    st.builds(
        lambda name, emails: f"{name}: {emails};",
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=20),
        st.lists(email_address, min_size=1, max_size=3).map(", ".join),
    ),
)

# Strategy for arbitrary address-like strings (more chaotic)
chaotic_address = st.one_of(
    email_with_name,
    group_syntax,
    st.text(max_size=500),  # Completely random text
    st.binary(max_size=200).map(
        lambda b: b.decode("utf-8", errors="replace")
    ),  # Random bytes as text
    st.lists(st.one_of(email_with_name, group_syntax), min_size=1, max_size=10).map(
        ", ".join
    ),
)

# Even more chaotic - includes null bytes, control chars, unicode edge cases
evil_text = st.one_of(
    st.text(max_size=2000),
    st.binary(max_size=1000).map(
        lambda b: b.decode("latin-1")
    ),  # All byte values as chars
    st.text(
        alphabet=st.characters(blacklist_categories=()), max_size=1000
    ),  # All unicode
    st.just("\x00" * 100),  # Null bytes
    st.just("\r\n" * 50),  # CRLF spam
    st.just("<" * 100 + ">" * 100),  # Angle bracket spam
    st.just("@" * 100),  # At sign spam
    st.just(":" * 50 + ";" * 50),  # Group syntax chars
    st.text(
        alphabet="\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d",
        max_size=200,
    ),  # Control chars
)

# Header-specific evil inputs - things that commonly appear in email headers
header_evil = st.one_of(
    # RFC 2047 encoded words - malformed variants
    st.builds(
        lambda charset, encoding, text: f"=?{charset}?{encoding}?{text}?=",
        st.text(max_size=20),
        st.sampled_from(["Q", "B", "q", "b", "", "X", "QQ", "?", "\x00"]),
        st.text(max_size=100),
    ),
    # Nested encoded words
    st.builds(
        lambda t: f"=?UTF-8?Q?=3D=3FUTF-8=3FQ=3F{t}=3F=3D?=",
        st.text(max_size=50),
    ),
    # Long header folding
    st.lists(st.text(max_size=100), min_size=1, max_size=20).map("\r\n ".join),
    # Header injection attempts
    st.builds(
        lambda h, v: f"legitimate\r\n{h}: {v}",
        st.sampled_from(["Bcc", "From", "Subject", "X-Injected"]),
        st.text(max_size=50),
    ),
    # Very long unfolded lines
    st.text(min_size=1000, max_size=10000),
    # Unicode edge cases
    st.just("\ufeff" * 10),  # BOM spam
    st.just("\u200b" * 100),  # Zero-width space
    st.just("\u202e" + "evil" + "\u202c"),  # RTL override
    st.just("\ud800"),  # Lone surrogate (invalid)
    st.just("\udfff"),  # Lone surrogate (invalid)
    st.just("A\u0300" * 100),  # Combining chars
    st.text(alphabet="\u200b\u200c\u200d\u2060\ufeff", max_size=200),  # Invisible chars
    # Mixed encodings in same header
    st.builds(
        lambda a, b: f"=?UTF-8?Q?{a}?= =?ISO-8859-1?B?{b}?=",
        st.text(max_size=30),
        st.text(max_size=30),
    ),
    # Quotes and escapes
    st.just('"\\"\\"\\"" <test@test.com>'),
    st.just("'\\''\\''' <test@test.com>"),
    st.builds(
        lambda n: '"' + '\\"' * n + '"',
        st.integers(min_value=0, max_value=100),
    ),
    # Comment variations (RFC 5322)
    st.builds(
        lambda c: f"(comment {c}) name <test@test.com>",
        st.text(max_size=100),
    ),
    st.builds(
        lambda n: "(" * n + "nested" + ")" * n,
        st.integers(min_value=0, max_value=50),
    ),
    # Address literals
    st.just("user@[127.0.0.1]"),
    st.just("user@[IPv6:::1]"),
    st.builds(
        lambda ip: f"user@[{ip}]",
        st.text(max_size=50),
    ),
    # Extremely long local parts and domains
    st.builds(
        lambda l, d: f"{l}@{d}",
        st.text(min_size=100, max_size=500),
        st.text(min_size=100, max_size=500),
    ),
    # Null in various positions
    st.just("test\x00@example.com"),
    st.just("test@\x00example.com"),
    st.just("\x00test@example.com"),
    st.just("Name \x00 <test@example.com>"),
    # Line breaks in addresses
    st.just("test@exam\r\nple.com"),
    st.just("test@exam\nple.com"),
    st.just("Na\r\nme <test@example.com>"),
    # Backslash escapes
    st.builds(
        lambda n: "\\" * n + "test@example.com",
        st.integers(min_value=0, max_value=50),
    ),
)


@pytest.mark.fuzz
class TestAddressParserFuzzing:
    """Fuzz tests for email address parsing functions."""

    @given(address=chaotic_address)
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_address_never_crashes(self, address):
        """parse_email_address should never crash on any input."""
        result = parse_email_address(address)

        # Should always return a tuple of two strings
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    @given(address=evil_text)
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_address_evil_input(self, address):
        """parse_email_address should handle evil input."""
        result = parse_email_address(address)
        assert isinstance(result, tuple)
        assert len(result) == 2

    @given(addresses=chaotic_address)
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_addresses_never_crashes(self, addresses):
        """parse_email_addresses should never crash on any input."""
        result = parse_email_addresses(addresses)

        # Should always return a list
        assert isinstance(result, list)

        # Each item should be a tuple of two strings
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], str)

    @given(addresses=evil_text)
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_addresses_evil_input(self, addresses):
        """parse_email_addresses should handle evil input."""
        result = parse_email_addresses(addresses)
        assert isinstance(result, list)

    @given(text=evil_text)
    @settings(**FUZZ_SETTINGS)
    def test_decode_email_header_text_never_crashes(self, text):
        """decode_email_header_text should never crash on any input."""
        result = decode_email_header_text(text)
        assert isinstance(result, str)

    @given(date_str=evil_text)
    @settings(**FUZZ_SETTINGS)
    def test_parse_date_never_crashes(self, date_str):
        """parse_date should never crash on any input."""
        result = parse_date(date_str)
        assert result is None or hasattr(result, "year")


@pytest.mark.fuzz
class TestAddressEdgeCasesFuzzing:
    """Fuzz tests targeting specific email address edge cases."""

    @given(
        prefix=st.text(max_size=100),
        suffix=st.sampled_from(
            [":;", ":>", ":", ";", ">", "", ":<", ":@", ": ;", ": >", ";;", ">>"]
        ),
    )
    @settings(**FUZZ_SETTINGS)
    def test_group_syntax_variants(self, prefix, suffix):
        """Test various group syntax patterns."""
        address = f"{prefix}{suffix}"
        result = parse_email_addresses(address)
        assert isinstance(result, list)

    @given(
        name=evil_text,
        quote_style=st.sampled_from(["'", '"', "", "`", "''", '""']),
    )
    @settings(**FUZZ_SETTINGS)
    def test_quoted_names(self, name, quote_style):
        """Test various quoting styles for display names."""
        if quote_style:
            address = f"{quote_style}{name}{quote_style} <test@example.com>"
        else:
            address = f"{name} <test@example.com>"

        result = parse_email_address(address)
        assert isinstance(result, tuple)
        assert len(result) == 2

        # If we used single quotes, they should be stripped
        if quote_style == "'" and name and "'" not in name:
            assert not result[0].startswith("'")
            assert not result[0].endswith("'")

    @given(
        encoded_part=st.text(max_size=200),
        encoding=st.sampled_from(
            ["UTF-8", "ISO-8859-1", "UNKNOWN", "", "utf8", "ASCII"]
        ),
        method=st.sampled_from(["Q", "B", "q", "b", "", "X"]),
    )
    @settings(**FUZZ_SETTINGS)
    def test_encoded_headers(self, encoded_part, encoding, method):
        """Test RFC 2047 encoded header patterns."""
        header = f"=?{encoding}?{method}?{encoded_part}?= <test@example.com>"
        result = parse_email_address(header)
        assert isinstance(result, tuple)

    @given(
        num_recipients=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=500, deadline=None)
    def test_many_recipients(self, num_recipients):
        """Test parsing many recipients."""
        recipients = ", ".join([f"user{i}@example.com" for i in range(num_recipients)])
        result = parse_email_addresses(recipients)
        assert isinstance(result, list)
        if num_recipients > 0:
            assert len(result) == num_recipients

    @given(
        local=st.text(max_size=100),
        domain=st.text(max_size=100),
    )
    @settings(**FUZZ_SETTINGS)
    def test_malformed_email_addresses(self, local, domain):
        """Test malformed email address patterns."""
        patterns = [
            f"{local}@{domain}",
            f"{local}@@{domain}",
            f"@{domain}",
            f"{local}@",
            f"<{local}@{domain}>",
            f"{local}@{domain}@extra",
            f"  {local}@{domain}  ",
        ]
        for pattern in patterns:
            result = parse_email_address(pattern)
            assert isinstance(result, tuple)
            assert len(result) == 2

    @given(
        chars=st.text(alphabet="<>()[]@:;,\"'\\", max_size=50),
    )
    @settings(**FUZZ_SETTINGS)
    def test_special_character_combinations(self, chars):
        """Test combinations of special email characters."""
        result = parse_email_address(chars)
        assert isinstance(result, tuple)
        result = parse_email_addresses(chars)
        assert isinstance(result, list)

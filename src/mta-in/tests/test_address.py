"""Unit tests for :mod:`pymta.address`.

Exercises every rejection branch in ``validate_envelope_address`` plus the
positive happy paths. Pure-stdlib — no Docker stack needed.
"""

from __future__ import annotations

import pytest

from pymta.address import AddressError, strip_brackets, validate_envelope_address


def _validate(address: str, *, allow_empty: bool = False) -> str:
    return validate_envelope_address(
        address, allow_empty=allow_empty, max_local=64, max_domain=255
    )


# ---------------------------------------------------------------------------
# strip_brackets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<user@example.com>", "user@example.com"),
        ("user@example.com", "user@example.com"),
        ("", ""),
        ("<>", ""),
    ],
)
def test_strip_brackets(raw, expected):
    assert strip_brackets(raw) == expected


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "user@example.com",
        "<user@example.com>",
        "user.name@example.com",
        "user+tag@sub.example.com",
        "a@b.co",
        "user@xn--bcher-kva.example",  # IDN A-label
        "user@münchen.de",  # raw UTF-8 domain (SMTPUTF8)
    ],
)
def test_valid_addresses_accepted(address):
    out = _validate(address)
    assert "@" in out


def test_domain_is_lowercased():
    assert _validate("User@EXAMPLE.COM") == "User@example.com"


def test_local_part_case_is_preserved():
    # Per RFC 5321 §2.3.11 local-parts are case-sensitive on the wire; we
    # leave the decision to the MDA's normalisation rules.
    assert _validate("User.Name@example.com").startswith("User.Name@")


def test_null_sender_allowed_when_enabled():
    assert _validate("", allow_empty=True) == ""
    assert _validate("<>", allow_empty=True) == ""


def test_null_sender_rejected_for_rcpt():
    with pytest.raises(AddressError) as exc:
        _validate("", allow_empty=False)
    assert exc.value.reason == "bad_address"
    assert exc.value.smtp_code == 553


# ---------------------------------------------------------------------------
# Residual / unbalanced angle brackets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "<<user@example.com>>",
        "<user@example.com",
        "user@example.com>",
        "<user@example.com>extra",
    ],
)
def test_residual_brackets_rejected(address):
    with pytest.raises(AddressError) as exc:
        _validate(address)
    assert exc.value.reason == "bad_address"
    assert exc.value.smtp_code == 501


# ---------------------------------------------------------------------------
# Control characters / CRLF injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "user\r@example.com",
        "user\n@example.com",
        "user\x00@example.com",
        "user\t@example.com",
        "user @example.com",
        "user%@example.com",
        "user\x7f@example.com",
    ],
)
def test_control_chars_rejected(address):
    with pytest.raises(AddressError) as exc:
        _validate(address)
    assert exc.value.reason == "control_char"
    assert exc.value.smtp_code == 501


# ---------------------------------------------------------------------------
# Source routes (RFC 5321 §4.1.1.3)
# ---------------------------------------------------------------------------


def test_source_route_rejected():
    with pytest.raises(AddressError) as exc:
        _validate("@host1.example,@host2.example:user@host3.example")
    assert exc.value.reason == "source_route"


# ---------------------------------------------------------------------------
# Quoted local-parts
# ---------------------------------------------------------------------------


def test_quoted_local_part_rejected():
    # The space-in-quote form would be caught earlier by the control-char
    # check; use a tame quoted form so we land on the quoted-local-part rule.
    with pytest.raises(AddressError) as exc:
        _validate('"weird"@example.com')
    assert exc.value.reason == "bad_address"
    assert exc.value.smtp_code == 553


# ---------------------------------------------------------------------------
# Dot-placement in unquoted local-part
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        ".user@example.com",
        "user.@example.com",
        "user..name@example.com",
    ],
)
def test_bad_dot_placement_rejected(address):
    with pytest.raises(AddressError) as exc:
        _validate(address)
    assert exc.value.reason == "bad_address"


# ---------------------------------------------------------------------------
# @-count, missing local/domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "noatsign",
        "two@@example.com",
        "user@host@other",
        "@example.com",  # caught earlier as source_route — covered above
        "user@",
    ],
)
def test_malformed_at_or_missing_parts_rejected(address):
    with pytest.raises(AddressError):
        _validate(address)


# ---------------------------------------------------------------------------
# Length limits
# ---------------------------------------------------------------------------


def test_overlong_local_part_rejected():
    long_local = "a" * 65
    with pytest.raises(AddressError) as exc:
        _validate(f"{long_local}@example.com")
    assert exc.value.reason == "oversize_local"


def test_overlong_domain_rejected():
    # 64-char label * 4 + dots = ~260 chars (>255 total domain cap).
    long_domain = ".".join(["a" * 60] * 5)
    with pytest.raises(AddressError) as exc:
        _validate(f"user@{long_domain}")
    assert exc.value.reason == "oversize_domain"


def test_overlong_label_rejected():
    too_long_label = "a" * 64  # one octet over RFC 1035 §2.3.4
    with pytest.raises(AddressError) as exc:
        _validate(f"user@{too_long_label}.example")
    assert exc.value.reason == "bad_address"


# ---------------------------------------------------------------------------
# Domain shape
# ---------------------------------------------------------------------------


def test_address_literal_rejected_with_dedicated_reason():
    with pytest.raises(AddressError) as exc:
        _validate("user@[192.0.2.1]")
    assert exc.value.reason == "address_literal"
    assert "literal" in exc.value.smtp_text.lower()


@pytest.mark.parametrize(
    "domain",
    [
        ".example.com",
        "example.com.",
        "example..com",
        ".",
    ],
)
def test_malformed_domain_rejected(domain):
    with pytest.raises(AddressError):
        _validate(f"user@{domain}")

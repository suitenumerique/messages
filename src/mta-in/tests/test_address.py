"""Unit tests for :mod:`pymta.address`.

Exercises every rejection branch in ``validate_envelope_address`` plus the
positive happy paths. Pure-stdlib — no Docker stack needed.
"""

from __future__ import annotations

import pytest
from jmap_email import is_valid_addr_spec

from pymta.address import AddressError, strip_brackets, validate_envelope_address


def _validate(address: str, *, allow_empty: bool = False, max_local: int | None = 64) -> str:
    return validate_envelope_address(
        address,
        allow_empty=allow_empty,
        max_local=max_local,
        max_domain=255,
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
        # Line terminators for readers other than logfmt's. SMTPUTF8 makes the
        # local-part arbitrary UTF-8, so these arrive from the wire, and
        # str.splitlines() breaks on every one of them: an address carrying one
        # is a forged record in any consumer that splits that way.
        "user @example.com",
        "user @example.com",
        "user\x85@example.com",
        "user\x0b@example.com",
        "user\x0c@example.com",
        "user\x1c@example.com",
        "user\x1b@example.com",
        # In the domain half too, which is the part we lower-case and hand on.
        "user@exam ple.com",
    ],
)
def test_control_chars_rejected(address):
    with pytest.raises(AddressError) as exc:
        _validate(address)
    assert exc.value.reason == "control_char"
    assert exc.value.smtp_code == 501


@pytest.mark.parametrize(
    "address",
    ["josé@example.com", "wörter@例え.テスト", "user+tag@example.com"],
)
def test_printable_non_ascii_still_accepted(address):
    """The rule is unprintable, not non-English. SMTPUTF8 has to keep working."""
    assert _validate(address)


@pytest.mark.parametrize(
    "address",
    ["maryam‌ahmadi@example.com", "nams‍te@example.com"],
)
def test_zero_width_format_characters_are_accepted(address):
    """Category Cf, which ``str.isprintable`` refuses along with the line
    terminators we want. Required in Persian and several Indic scripts, so a
    sender's RFC 6531 MAIL FROM may carry one."""
    assert _validate(address)


@pytest.mark.parametrize(
    "code_point",
    [
        0x202E,  # RIGHT-TO-LEFT OVERRIDE: "user<RLO>moc.elpmaxe@" reads backwards
        0x202A,  # LEFT-TO-RIGHT EMBEDDING
        0x2066,  # LEFT-TO-RIGHT ISOLATE
        0x2069,  # POP DIRECTIONAL ISOLATE
        0x200E,  # LEFT-TO-RIGHT MARK
        0x200F,  # RIGHT-TO-LEFT MARK
        0x061C,  # ARABIC LETTER MARK
        0x200B,  # ZERO WIDTH SPACE
        0x2060,  # WORD JOINER
        0xFEFF,  # BOM
        0x00AD,  # SOFT HYPHEN
        0xE0041,  # TAG LATIN CAPITAL LETTER A
        0x0600,  # ARABIC NUMBER SIGN: Cf, though visible
    ],
)
@pytest.mark.parametrize("template", ["us{}er@example.com", "user@exam{}ple.com"])
def test_every_other_format_character_is_refused(code_point, template):
    """Invisible, and of no use in an address but to make it display as another.

    Only the two join controls are let in, as in IDNA2008 and PRECIS.
    Written as code points: the characters themselves would hide in this file.
    """
    with pytest.raises(AddressError) as exc:
        _validate(template.format(chr(code_point)))
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


@pytest.mark.parametrize(
    "address", ['"we ird"@example.com', '"we(ird"@example.com', '"a..b"@example.com']
)
def test_quoted_local_part_rejected(address):
    """On a recipient, quotes the content needs: no mailbox has such a name."""
    with pytest.raises(AddressError) as exc:
        _validate(address)
    assert exc.value.reason == "bad_address"
    assert exc.value.smtp_code == 553


def test_quotes_the_content_does_not_need_are_dropped():
    """RFC 5321 section 4.1.2: ``"bob"@x`` and ``bob@x`` are the same mailbox."""
    assert _validate('"weird"@example.com') == "weird@example.com"


@pytest.mark.parametrize("local", ["a(b)", "a,b", "a;b", "a:b", "a\\b", "a[b", 'a"b"c'])
def test_specials_outside_quotes_are_refused(local):
    with pytest.raises(AddressError) as exc:
        _validate(f"{local}@example.com")
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


def test_overlong_local_part_accepted_without_local_cap():
    # A forwarder's SRS-wrapped bounce address, longer than the RFC's
    # 256-octet path too: neither limit applies to MAIL FROM.
    srs = "SRS0=Ab3d=TT=bounces.mailer.example=" + "b" * 300 + "@forwarder.example"
    assert _validate(srs, max_local=None) == srs


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


# DNS limits apply to the A-label, not to the UTF-8 an SMTPUTF8 peer sends:
# each of these labels is over 63 UTF-8 octets but a valid A-label.
@pytest.mark.parametrize(
    "label",
    [
        "министерство-экономики-и-финансов-россии",  # 76 octets, A-label 52
        "وزارةالاقتصادوالماليةالعامةللدولة",  # 66 octets, A-label 46
        "é" * 55,  # 110 octets, A-label 61
    ],
)
def test_idn_label_measured_as_a_label(label):
    address = f"user@{label}.example"
    assert _validate(address) == address


def test_idn_domain_total_measured_as_a_label():
    # 443 UTF-8 octets, 247 as A-labels.
    domain = ".".join(["é" * 55] * 4)
    assert _validate(f"user@{domain}") == f"user@{domain}"


def test_idn_label_over_63_octets_as_a_label_rejected():
    label = "中华人民共和国国家发展和改革委员会办公厅秘书"  # 66 octets, A-label 65
    with pytest.raises(AddressError) as exc:
        _validate(f"user@{label}.example")
    assert exc.value.reason == "bad_address"


def test_label_left_empty_by_uts46_mapping_rejected():
    # U+3002 maps to ".", so this is "a..example" once mapped.
    with pytest.raises(AddressError) as exc:
        _validate("user@a。.example")
    assert exc.value.reason == "bad_address"


def test_ideographic_full_stop_between_labels_accepted():
    assert _validate("user@a。example.com") == "user@a。example.com"


@pytest.mark.parametrize("domain", ["faß.de", "mail_relay.example.com"])
def test_domain_length_check_does_not_apply_idna_validity(domain):
    # ``ß`` must survive (IDNA2003 would fold it to ``ss``) and ``_``, refused
    # by IDNA, has always been accepted: only lengths are checked.
    assert _validate(f"user@{domain}") == f"user@{domain}"


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


@pytest.mark.parametrize(
    "domain",
    ["[", "]", "[192.0.2.1", "192.0.2.1]", "exa[mple.com", "a.]b", '"', 'exa"mple.com']
    + [f"exa{char}mple.com" for char in "!#$&'()*+,/:;=?\\^`{|}~"],
)
def test_ascii_outside_a_host_name_is_not_a_domain(domain):
    """Found by the fuzz suite: the domain's characters were not checked, only
    its length, its dots and a balanced ``[...]``."""
    with pytest.raises(AddressError) as exc:
        _validate(f"user@{domain}")
    assert exc.value.reason == "bad_address"


# ---------------------------------------------------------------------------
# ``%`` in the local-part: senders only (VERP), never recipients.
# ---------------------------------------------------------------------------


def _validate_sender(address: str, **options) -> str:
    return validate_envelope_address(
        address, allow_empty=True, max_local=None, max_domain=255, allow_percent=True, **options
    )


@pytest.mark.parametrize(
    "address", ["bounce-first.last%40town.example@esp.example", "a%b%c@example.com"]
)
def test_percent_is_accepted_in_a_sender_local_part(address):
    assert _validate_sender(address) == address


def test_percent_is_refused_unless_allowed():
    with pytest.raises(AddressError) as exc:
        _validate("bounce-first.last%40town.example@esp.example")
    assert exc.value.reason == "control_char"


@pytest.mark.parametrize("address", ["user@exa%mple.com", "user%x@exa%mple.com", "a%b"])
def test_percent_is_never_accepted_in_the_domain(address):
    with pytest.raises(AddressError):
        _validate_sender(address)


def test_allowing_percent_allows_nothing_else():
    for address in ("us er%x@example.com", "user%x\r@example.com", '"a %b"@example.com'):
        with pytest.raises(AddressError):
            _validate_sender(address)


# ---------------------------------------------------------------------------
# Single-label domains.
# ---------------------------------------------------------------------------


def test_a_single_label_domain_is_accepted_by_default():
    assert _validate("root@localhost") == "root@localhost"


def test_a_single_label_domain_is_refused_when_a_fqdn_is_required():
    with pytest.raises(AddressError) as exc:
        _validate_sender("root@localhost", require_fqdn=True)
    assert (exc.value.reason, exc.value.smtp_code) == ("non_fqdn_sender", 504)


def test_requiring_a_fqdn_leaves_the_null_sender_and_real_domains_alone():
    assert _validate_sender("", require_fqdn=True) == ""
    assert _validate_sender("a@example.com", require_fqdn=True) == "a@example.com"


# ---------------------------------------------------------------------------
# Address literals, where the caller allows them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "clean"),
    [
        ("a@[192.0.2.1]", "a@[192.0.2.1]"),
        ("a@[IPv6:2001:DB8:0:0::1]", "a@[IPv6:2001:db8::1]"),
        ("a@[ipv6:::1]", "a@[IPv6:::1]"),
    ],
)
def test_an_address_literal_is_normalised(address, clean):
    assert _validate_sender(address, allow_literal=True) == clean
    assert _validate_sender(clean, allow_literal=True) == clean


@pytest.mark.parametrize(
    "address",
    [
        "a@[192.0.2.999]",
        "a@[192.0.2]",
        "a@[IPv6:zz::1]",
        "a@[2001:db8::1]",
        "a@[example.com]",
        "a@[]",
    ],
)
def test_a_malformed_address_literal_is_refused(address):
    with pytest.raises(AddressError) as exc:
        _validate_sender(address, allow_literal=True)
    assert exc.value.reason == "address_literal"
    assert "Malformed address literal" in exc.value.smtp_text


# ---------------------------------------------------------------------------
# The contract with the backend, which reads addresses with jmap_email: what
# we accept and forward must be one mailbox to it too. The fuzz suite holds
# the same property over generated input (test_address_fuzz.py).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "alice@example.com",
        "Alice.O'Neil+tag@Sub.Example.ORG",
        "a%b@example.com",
        "a..b@example.com",
        ".alice.@example.com",
        '"alice"@example.com',
        '"al ice"@example.com',
        '"a@b"@example.com',
        '"a\\"b,c;d"@example.com',
        '"a(b)[c]"@example.com',
        "alice@[192.0.2.1]",
        "alice@[IPv6:2001:DB8::1]",
        "alice@localhost",
        "élise@exämple.com",
        "alice@xn--bcher-kva.example",
    ],
)
def test_every_accepted_sender_is_one_mailbox_to_the_backend(address):
    clean = _validate_sender(address, allow_quoted=True, allow_literal=True)
    assert is_valid_addr_spec(clean), clean


@pytest.mark.parametrize(
    "address",
    ["bob@example.com", "Bob+tag@Example.COM", '"bob"@example.com', "bøb@example.com"],
)
def test_every_accepted_recipient_is_one_mailbox_to_the_backend(address):
    assert is_valid_addr_spec(_validate(address))

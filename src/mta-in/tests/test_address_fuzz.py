"""Fuzzing the envelope-address validator and the source key.

Every address a peer sends goes through ``validate_envelope_address`` before
anything else sees it, so its contract is the contract of everything
downstream: it returns a clean address or raises ``AddressError``, nothing
else, and what it returns is safe to log, to send to the MDA and to put in a
header.

Run with: make fuzz-mta-in-py
"""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jmap_email import is_valid_addr_spec

from pymta import settings as pymta_settings
from pymta.address import AddressError, strip_brackets, validate_envelope_address
from pymta.command import split_path
from pymta.limits import source_key

from tests.fuzzing import fuzz_settings

pytestmark = pytest.mark.fuzz

# Address-shaped text finds more than uniform text does: most random strings
# fail at the first check.
_ATOM = st.text(
    alphabet=st.one_of(
        st.sampled_from(list("abcXYZ019.-_+%=\"'<>@[]: \t\r\n\x00\x7f")),
        st.characters(),
    ),
    max_size=40,
)
_DOMAIN = st.sampled_from(
    [
        "example.com",
        "Sub.Example.ORG",
        "localhost",
        "xn--bcher-kva.example",
        "münchen.de",
        "a_b.io",
    ]
)
_ADDRESS = st.one_of(
    st.text(max_size=120),
    # A domain that passes, so the local-part rules are what decides.
    st.builds(lambda local, domain: f"{local}@{domain}", _ATOM, _DOMAIN),
    st.builds(
        lambda local, domain: f"{local}@{domain}",
        st.text(alphabet=st.sampled_from(list("ab.%+-_")), min_size=1, max_size=8),
        _DOMAIN,
    ),
    st.builds(lambda local, domain: f"{local}@{domain}", _ATOM, _ATOM),
    st.builds(lambda local, domain: f"<{local}@{domain}>", _ATOM, _ATOM),
    # As the command parser decodes a byte that is not UTF-8.
    st.binary(max_size=60).map(lambda raw: raw.decode("utf-8", "surrogateescape")),
)

_OPTIONS = st.fixed_dictionaries(
    {
        "allow_empty": st.booleans(),
        "max_local": st.sampled_from([None, 64]),
        "allow_percent": st.booleans(),
        "require_fqdn": st.booleans(),
    }
)

_UNSAFE_CATEGORIES = {"Cc", "Zl", "Zp", "Zs"}


def _validate(address, options):
    return validate_envelope_address(address, max_domain=255, **options)


@settings(**fuzz_settings())
@given(address=_ADDRESS, options=_OPTIONS)
def test_validation_returns_an_address_or_raises_address_error(address, options):
    try:
        clean = _validate(address, options)
    except AddressError as err:
        # The reply is built from these.
        assert 500 <= err.smtp_code <= 599
        assert err.reason
        assert "\r" not in err.smtp_text and "\n" not in err.smtp_text
        return

    if clean == "":
        assert options["allow_empty"]
        return

    # Storable and loggable.
    clean.encode("utf-8")
    assert not any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in clean)
    assert not set(clean) & set('<>" \x7f')

    local, at, domain = clean.rpartition("@")
    assert at and local and domain
    assert "@" not in local
    assert domain == domain.lower()
    assert not (local.startswith(".") or local.endswith(".") or ".." in local)
    assert not (domain.startswith(".") or domain.endswith(".") or ".." in domain)
    assert all(not c.isascii() or c.isalnum() or c in "-_." for c in domain), domain
    if not options["allow_percent"]:
        assert "%" not in clean
    if options["max_local"] is not None:
        assert len(local.encode("utf-8")) <= options["max_local"]
    if options["require_fqdn"]:
        assert "." in domain


@settings(**fuzz_settings())
@given(address=_ADDRESS, options=_OPTIONS)
def test_an_accepted_address_validates_to_itself(address, options):
    """What we hand to the MDA would be accepted again as it stands."""
    try:
        clean = _validate(address, options)
    except AddressError:
        return
    assert _validate(clean, options) == clean


@settings(**fuzz_settings())
@given(address=_ADDRESS)
def test_what_rcpt_accepts_mail_accepts(address):
    """MAIL FROM is the more lenient of the two, never the reverse."""
    try:
        clean = validate_envelope_address(
            address,
            allow_empty=False,
            max_local=pymta_settings.PYMTA_MAX_LOCAL_PART,
            max_domain=pymta_settings.PYMTA_MAX_DOMAIN,
        )
    except AddressError:
        return
    assert (
        validate_envelope_address(
            address,
            allow_empty=True,
            max_local=None,
            max_domain=pymta_settings.PYMTA_MAX_DOMAIN,
            allow_percent=True,
        )
        == clean
    )
    # With the sender-only forms on as well it is still accepted, possibly
    # written in quotes.
    _as_sender(address)


def _as_sender(address: str) -> str:
    return validate_envelope_address(
        address,
        allow_empty=True,
        max_local=None,
        max_domain=pymta_settings.PYMTA_MAX_DOMAIN,
        allow_percent=True,
        allow_quoted=True,
        allow_literal=True,
    )


_QUOTED = st.builds(
    lambda content, domain: f'"{content}"@{domain}',
    st.text(
        alphabet=st.one_of(st.sampled_from(list('ab .@\\"%()<>\t\r\x00')), st.characters()),
        max_size=12,
    ),
    st.one_of(_DOMAIN, st.sampled_from(["[192.0.2.1]", "[IPv6:2001:DB8::1]", "[x]", "[1.2.3]"])),
)


@settings(**fuzz_settings())
@given(address=st.one_of(_ADDRESS, _QUOTED, _QUOTED))
def test_a_sender_is_refused_or_comes_out_in_one_canonical_form(address):
    try:
        clean = _as_sender(address)
    except AddressError:
        return
    if clean == "":
        return

    clean.encode("utf-8")
    # Nothing that breaks a line or a header, anywhere. A plain space only
    # inside a quoted local-part.
    assert not any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in clean)
    assert "\x7f" not in clean
    assert all(unicodedata.category(c) != "Zs" or c == " " for c in clean)
    local, _, domain = clean.rpartition("@")
    assert local and domain
    if " " in clean or '"' in clean:
        assert local.startswith('"') and local.endswith('"') and " " not in domain
    if domain.startswith("["):
        assert domain.endswith("]")
    else:
        assert domain == domain.lower()
        assert all(not c.isascii() or c.isalnum() or c in "-_." for c in domain), domain
    # Forwarded as it stands, it would be accepted again, unchanged.
    assert _as_sender(clean) == clean


@settings(**fuzz_settings())
@given(address=st.one_of(_ADDRESS, _QUOTED, _QUOTED))
def test_an_accepted_sender_reads_back_the_same_from_the_wire(address):
    """Through the command parser, the validator, and through both again.

    The validator drops some quotes and adds others, so this is where a sender
    could come out as a different address the second time: when a bounce or a
    relay puts what we stored back into a MAIL FROM.
    """

    def parse(path):
        split = split_path(f"<{path}>")
        return split[0] if split else None

    first = parse(address)
    if not first or first == "<>":
        return
    try:
        clean = _as_sender(first)
    except AddressError:
        return
    again = parse(clean)
    assert again is not None, clean
    assert _as_sender(again) == clean


# ---------------------------------------------------------------------------
# The contract with the backend.
#
# The MDA reads addresses with jmap_email. What we accept and forward must be
# one mailbox to it too: an address the MTA takes and the backend reads as
# two, or as none, is where a message goes to the wrong place.
# ---------------------------------------------------------------------------


@settings(**fuzz_settings())
@given(address=st.one_of(_ADDRESS, _QUOTED, _QUOTED), options=_OPTIONS)
def test_an_accepted_address_is_one_mailbox_to_the_backend(address, options):
    for validate in (_as_sender, lambda value: _validate(value, options)):
        try:
            clean = validate(address)
        except AddressError:
            continue
        # "" is the null sender: no mailbox at all, and never a recipient.
        assert clean == "" or is_valid_addr_spec(clean), clean


@settings(**fuzz_settings())
@given(raw=st.text(max_size=80))
def test_strip_brackets_removes_at_most_one_pair(raw):
    stripped = strip_brackets(raw)
    assert stripped in (raw, raw[1:-1])
    if stripped != raw:
        assert raw.startswith("<") and raw.endswith(">")


@settings(**fuzz_settings())
@given(
    ip=st.one_of(
        st.text(max_size=60),
        st.ip_addresses().map(str),
        st.ip_addresses(v=4).map(lambda ip: f"::ffff:{ip}"),
    )
)
def test_source_key_is_total_and_stable(ip):
    """A PROXY header can claim anything; the gate key must not raise on it,
    and must not split one source into several."""
    key = source_key(ip)
    assert isinstance(key, str)
    assert source_key(key) == key

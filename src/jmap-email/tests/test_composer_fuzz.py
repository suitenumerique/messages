"""
Fuzzing tests for RFC 5322 email composition.

Property-based tests for every user-controlled input path into the composer.
Each test uses Hypothesis to generate chaotic inputs and verifies that the
composer either produces well-formed RFC 5322 bytes OR raises a structured
ComposeError — never an unwrapped stdlib exception, never a crash, never
a malformed output.

Input paths covered:
  - compose_email(jmap_data, in_reply_to, prepend_headers, keep_bcc)
      ↳ jmap_data fields: from, to, cc, bcc, subject, date, messageId,
        references, textBody, htmlBody, attachments, headers
      ↳ in_reply_to: arbitrary string
      ↳ prepend_headers: arbitrary list of (str, str) tuples
  - format_address(name, email)
  - format_address_list(addresses)
  - _normalize_date(date)

Run with: pytest -m fuzz core/tests/mda/test_rfc5322_composer_fuzz.py
Or:       make fuzz-back
"""

import base64
import copy
import datetime
import os
import re
from email import policy
from email.parser import BytesParser

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from jmap_email.composer import (
    ComposeError,
    _normalize_date,
    compose_email,
    format_address,
    format_address_list,
)

# Fuzzing settings. Default 2000 examples per test keeps a normal `make
# fuzz-back` run under ~3 minutes; bump via FUZZ_EXAMPLES=20000 for an
# intensive pass before releases or after composer changes.
FUZZ_SETTINGS = {
    "max_examples": int(os.environ.get("FUZZ_EXAMPLES", "2000")),
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    "phases": [Phase.generate, Phase.target],
}


# ---------- Strategies ----------------------------------------------------


# Up to 200 chars of arbitrary BMP — covers C0 controls, line terminators
# (incl. NEL/LS/PS), surrogates, RTL marks, the literal '=?...?=' that RFC
# 2047 decodes, etc.
chaotic_text = st.text(
    alphabet=st.characters(min_codepoint=0x00, max_codepoint=0xFFFF),
    max_size=200,
)

# Slightly more email-shaped string — biased toward producing parseable
# addresses, but allows garbage to leak in.
loose_email = st.one_of(
    st.text(
        alphabet=st.sampled_from(
            'abcdefghijklmnopqrstuvwxyz0123456789@.-_+ <>\r\n\t,;"\\'
        ),
        min_size=1,
        max_size=80,
    ),
    chaotic_text,
)

contact_dict = st.fixed_dictionaries(
    {
        "name": st.one_of(st.none(), chaotic_text),
        "email": st.one_of(st.none(), loose_email),
    }
)

contact_list = st.lists(contact_dict, max_size=4)

# Date: exercise every branch of _normalize_date.
chaotic_date = st.one_of(
    st.none(),
    st.datetimes(
        min_value=datetime.datetime(1970, 1, 1),
        max_value=datetime.datetime(2099, 12, 31),
    ),
    st.integers(min_value=-(2**31), max_value=2**31),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),  # Bool is technically int but we explicitly exclude it
    chaotic_text,
)

# An attachment dict — bytes, str, or garbage content; chaotic name/cid/type.
attachment_dict = st.fixed_dictionaries(
    {
        "content": st.one_of(
            st.binary(max_size=200),
            st.text(max_size=200),
            st.builds(
                lambda b: base64.b64encode(b).decode("ascii"),
                st.binary(max_size=200),
            ),
            st.none(),
        ),
        "type": st.one_of(
            st.sampled_from(
                [
                    "application/pdf",
                    "image/png",
                    "image/jpeg",
                    "text/plain",
                    "application/octet-stream",
                    "garbage",
                    "image/jpeg; name=evil.exe",
                    "",
                ]
            ),
            chaotic_text,
        ),
        "name": chaotic_text,
        "disposition": st.sampled_from(["attachment", "inline", "weird", "", "INLINE"]),
        "cid": st.one_of(st.none(), chaotic_text),
    }
)

# A jmap_data dict.
jmap_dict = st.fixed_dictionaries(
    {
        "from": st.one_of(contact_dict, st.lists(contact_dict, max_size=2)),
        "to": contact_list,
        "cc": contact_list,
        "bcc": contact_list,
        "subject": st.one_of(st.none(), chaotic_text),
        "sentAt": chaotic_date,
        "messageId": st.one_of(st.none(), chaotic_text),
        "references": st.one_of(st.none(), chaotic_text),
        "textBody": st.lists(
            st.one_of(
                chaotic_text,
                st.fixed_dictionaries({"content": chaotic_text}),
            ),
            max_size=3,
        ),
        "htmlBody": st.lists(
            st.one_of(
                chaotic_text,
                st.fixed_dictionaries({"content": chaotic_text}),
            ),
            max_size=3,
        ),
        "attachments": st.lists(attachment_dict, max_size=3),
        "headers": st.dictionaries(keys=chaotic_text, values=chaotic_text, max_size=3),
    }
)

prepend_headers_list = st.lists(st.tuples(chaotic_text, chaotic_text), max_size=3)


# ---------- Invariant helpers --------------------------------------------


def _assert_wire_format_invariants(raw):
    """Every byte sequence the composer emits must satisfy these properties.

    Failures here are real bugs — they mean a downstream SMTP relay,
    DKIM signer, or parser will misbehave on a message it should accept.
    """
    assert isinstance(raw, bytes), f"not bytes: {type(raw).__name__}"
    assert len(raw) > 0
    # 1. CRLF-only line endings (RFC 5321 §2.3.7).
    for i, ch in enumerate(raw):
        if ch == 0x0A:
            assert i > 0 and raw[i - 1] == 0x0D, f"bare LF at offset {i}"
        if ch == 0x0D:
            assert i + 1 < len(raw) and raw[i + 1] == 0x0A, f"bare CR at offset {i}"
    # 2. Hard line length cap (RFC 5322 §2.1.1).
    for line in raw.split(b"\r\n"):
        assert len(line) <= 998, f"line of {len(line)} octets exceeds 998"
    # 3. Header / body separator present.
    assert b"\r\n\r\n" in raw, "missing header/body separator"


def _assert_parseable_with_required_headers(raw):
    """The bytes must round-trip through stdlib's BytesParser without
    parser-defect flags, and have the headers we always emit."""
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert not parsed.defects, f"parser defects: {parsed.defects}"
    assert parsed["MIME-Version"] == "1.0"
    assert parsed["Date"] is not None
    # No header injection: certain reserved headers must not appear duplicated
    # via attacker-controlled values.
    for header in ("From", "To", "Subject", "Date", "MIME-Version"):
        # Either present once or not present (Cc / Bcc may be absent).
        count = len(parsed.get_all(header) or [])
        assert count <= 1, f"{header} appears {count} times"
    return parsed


# ---------- Tests --------------------------------------------------------


@pytest.mark.fuzz
class TestComposeEmailFuzz:
    """compose_email with arbitrary user-controlled JMAP dicts."""

    @given(jmap=jmap_dict)
    @settings(**FUZZ_SETTINGS)
    def test_compose_either_succeeds_or_raises_email_compose_error(self, jmap):
        """compose_email's contract: bytes on success, ComposeError on
        failure. Any other exception is a bug."""
        try:
            raw = compose_email(jmap)
        except ComposeError:
            return
        assert isinstance(raw, bytes) and len(raw) > 0

    @given(jmap=jmap_dict)
    @settings(**FUZZ_SETTINGS)
    def test_compose_output_is_wire_legal(self, jmap):
        """If compose succeeds, output meets every wire-format invariant."""
        try:
            raw = compose_email(jmap)
        except ComposeError:
            return
        _assert_wire_format_invariants(raw)
        _assert_parseable_with_required_headers(raw)

    @given(jmap=jmap_dict)
    @settings(**FUZZ_SETTINGS)
    def test_compose_drops_bcc_by_default(self, jmap):
        """RFC 5322 §3.6.3 contract: Bcc never leaks unless keep_bcc=True."""
        try:
            raw = compose_email(jmap)
        except ComposeError:
            return
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        assert parsed["Bcc"] is None

    @given(jmap=jmap_dict, in_reply_to=chaotic_text)
    @settings(**FUZZ_SETTINGS)
    def test_compose_with_in_reply_to(self, jmap, in_reply_to):
        """in_reply_to is the most user-controlled string in the API
        (PST import passes parsed inbound msg-ids straight through)."""
        try:
            raw = compose_email(jmap, in_reply_to=in_reply_to)
        except ComposeError:
            return
        _assert_wire_format_invariants(raw)

    @given(jmap=jmap_dict, prepend=prepend_headers_list)
    @settings(**FUZZ_SETTINGS)
    def test_compose_with_prepend_headers(self, jmap, prepend):
        """prepend_headers carries widget Referer / Received which is
        attacker-influenced. Reserved-name guard + ftext validation must
        either accept or raise ComposeError; nothing in between."""
        try:
            raw = compose_email(jmap, prepend_headers=prepend)
        except ComposeError:
            return
        _assert_wire_format_invariants(raw)
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        # Whatever made it through must not shadow reserved identity headers.
        for reserved in ("From", "To", "Subject", "Date"):
            assert len(parsed.get_all(reserved) or []) <= 1

    @given(jmap=jmap_dict, keep_bcc=st.booleans())
    @settings(**FUZZ_SETTINGS)
    def test_compose_keep_bcc_flag_is_honored(self, jmap, keep_bcc):
        """keep_bcc=True surfaces Bcc; False drops it. This is the contract
        PST import relies on."""
        try:
            raw = compose_email(jmap, keep_bcc=keep_bcc)
        except ComposeError:
            return
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        if not keep_bcc:
            assert parsed["Bcc"] is None
        # When keep_bcc=True, Bcc may or may not appear depending on whether
        # the input had any bcc entries with a non-empty email. Don't assert
        # presence — just that the flag is honored on the no-leak side.

    @given(jmap=jmap_dict)
    @settings(**FUZZ_SETTINGS)
    def test_compose_does_not_mutate_input(self, jmap):
        """Caller-provided dict must not be mutated by compose_email."""
        before = copy.deepcopy(jmap)
        try:
            compose_email(jmap)
        except ComposeError:
            pass
        assert jmap == before

    @given(jmap=jmap_dict)
    @settings(**FUZZ_SETTINGS)
    def test_compose_boundary_uniqueness_in_multipart(self, jmap):
        """Every multipart level must use a distinct boundary (RFC 2046 §5.1.1)."""
        try:
            raw = compose_email(jmap)
        except ComposeError:
            return
        boundaries = re.findall(rb'boundary="([^"]+)"', raw)
        assert len(set(boundaries)) == len(boundaries), (
            f"duplicate boundaries: {boundaries!r}"
        )


@pytest.mark.fuzz
class TestFormatAddressFuzz:
    """format_address / format_address_list with chaotic input."""

    @given(name=chaotic_text, email=loose_email)
    @settings(**FUZZ_SETTINGS)
    def test_format_address_returns_str_no_crash(self, name, email):
        """format_address never raises and always returns a str."""
        result = format_address(name, email)
        assert isinstance(result, str)

    @given(addrs=contact_list)
    @settings(**FUZZ_SETTINGS)
    def test_format_address_list_returns_str_no_crash(self, addrs):
        """format_address_list never raises and always returns a str."""
        result = format_address_list(addrs)
        assert isinstance(result, str)


@pytest.mark.fuzz
class TestNormalizeDateFuzz:
    """_normalize_date with chaotic input — every JMAP input shape."""

    @given(date=chaotic_date)
    @settings(**FUZZ_SETTINGS)
    def test_normalize_date_returns_tz_aware_or_raises(self, date):
        """_normalize_date is strict-by-design (M10): inputs it can parse
        return a tz-aware datetime; everything else (``None``, garbage
        strings, ints / floats out of POSIX range, bools, dicts) raises
        ``InvalidDateError``. The contract is "no fabricated `now()`
        fallback" — the caller controls the timestamp."""
        from jmap_email.composer import InvalidDateError

        try:
            result = _normalize_date(date)
        except InvalidDateError:
            return
        assert isinstance(result, datetime.datetime)
        # Must be tz-aware so format_datetime always emits a +HHMM offset.
        assert result.tzinfo is not None
        assert result.tzinfo.utcoffset(result) is not None


@pytest.mark.fuzz
class TestEndToEndPathFuzz:
    """Every realistic caller path: editor (text+html+attachments+inline),
    inbound-reply, PST-import (with bcc), widget (prepend_headers)."""

    editor_jmap = st.fixed_dictionaries(
        {
            "from": contact_dict,
            "to": contact_list,
            "cc": contact_list,
            "subject": chaotic_text,
            "sentAt": chaotic_date,
            "textBody": st.lists(chaotic_text, max_size=1),
            "htmlBody": st.lists(chaotic_text, max_size=1),
            "attachments": st.lists(attachment_dict, max_size=2),
        }
    )

    @given(jmap=editor_jmap)
    @settings(**FUZZ_SETTINGS)
    def test_editor_path_no_unexpected_exception(self, jmap):
        """Editor → outbound.compose_and_store_mime → compose_email."""
        try:
            raw = compose_email(jmap)
        except ComposeError:
            return
        _assert_wire_format_invariants(raw)

    pst_jmap = st.fixed_dictionaries(
        {
            "from": contact_dict,
            "to": contact_list,
            "cc": contact_list,
            "bcc": contact_list,  # PST import preserves Bcc via keep_bcc=True
            "subject": chaotic_text,
            "sentAt": chaotic_date,
            "messageId": st.one_of(st.none(), chaotic_text),
            "references": st.one_of(st.none(), chaotic_text),
            "textBody": st.lists(chaotic_text, max_size=1),
            "htmlBody": st.lists(chaotic_text, max_size=1),
            "attachments": st.lists(attachment_dict, max_size=2),
            "headers": st.dictionaries(chaotic_text, chaotic_text, max_size=2),
        }
    )

    @given(jmap=pst_jmap)
    @settings(**FUZZ_SETTINGS)
    def test_pst_import_path_with_keep_bcc(self, jmap):
        """PST import → reconstruct_eml → compose_email(keep_bcc=True)."""
        try:
            raw = compose_email(jmap, keep_bcc=True)
        except ComposeError:
            return
        _assert_wire_format_invariants(raw)

    widget_jmap = st.fixed_dictionaries(
        {
            "from": contact_dict,
            "to": contact_list,
            "subject": chaotic_text,
            "sentAt": st.datetimes(),
            "textBody": st.lists(chaotic_text, max_size=1),
            "htmlBody": st.lists(chaotic_text, max_size=1),
        }
    )

    @given(jmap=widget_jmap, prepend=prepend_headers_list)
    @settings(**FUZZ_SETTINGS)
    def test_widget_path_with_prepend_headers(self, jmap, prepend):
        """Widget → compose_email with prepend_headers from HTTP Referer
        and REMOTE_ADDR (attacker-influenced)."""
        try:
            raw = compose_email(jmap, prepend_headers=prepend)
        except ComposeError:
            return
        _assert_wire_format_invariants(raw)

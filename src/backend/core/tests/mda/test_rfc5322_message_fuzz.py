"""
Fuzzing tests for RFC5322 email message parsing.

These tests use hypothesis for property-based testing to find edge cases
and potential crashes in the complete email message parsing code.

Run with: pytest -m fuzz core/tests/mda/test_rfc5322_message_fuzz.py
Or: make fuzz-back
"""

import base64

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from core.mda.rfc5322.parser import EmailParseError, parse_email_message

# Intensive fuzzing settings
FUZZ_SETTINGS = {
    "max_examples": 10000,
    "deadline": None,  # No time limit per example
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    "phases": [Phase.generate, Phase.target],  # Skip shrinking for speed
}


# --- Reusable strategies ---

# Basic email components
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

email_address = st.builds(
    lambda local, domain: f"{local}@{domain}",
    email_local_part,
    email_domain,
)

display_name = st.text(min_size=0, max_size=100)

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
)

# Group syntax patterns
group_syntax = st.one_of(
    st.just("undisclosed-recipients:;"),
    st.just("undisclosed-recipients:>"),
    st.builds(
        lambda name: f"{name}:;",
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz -", min_size=1, max_size=30),
    ),
)

chaotic_address = st.one_of(
    email_with_name,
    group_syntax,
    st.text(max_size=500),
    st.binary(max_size=200).map(lambda b: b.decode("utf-8", errors="replace")),
    st.lists(st.one_of(email_with_name, group_syntax), min_size=1, max_size=10).map(
        ", ".join
    ),
)

# Evil text with edge cases
evil_text = st.one_of(
    st.text(max_size=2000),
    st.binary(max_size=1000).map(lambda b: b.decode("latin-1")),
    st.text(alphabet=st.characters(blacklist_categories=()), max_size=1000),
    st.just("\x00" * 100),
    st.just("\r\n" * 50),
    st.just("<" * 100 + ">" * 100),
    st.just("@" * 100),
    st.just(":" * 50 + ";" * 50),
    st.text(
        alphabet="\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d",
        max_size=200,
    ),
)

# Header-specific evil inputs
header_evil = st.one_of(
    st.builds(
        lambda charset, encoding, text: f"=?{charset}?{encoding}?{text}?=",
        st.text(max_size=20),
        st.sampled_from(["Q", "B", "q", "b", "", "X", "QQ", "?", "\x00"]),
        st.text(max_size=100),
    ),
    st.builds(
        lambda t: f"=?UTF-8?Q?=3D=3FUTF-8=3FQ=3F{t}=3F=3D?=",
        st.text(max_size=50),
    ),
    st.lists(st.text(max_size=100), min_size=1, max_size=20).map("\r\n ".join),
    st.builds(
        lambda h, v: f"legitimate\r\n{h}: {v}",
        st.sampled_from(["Bcc", "From", "Subject", "X-Injected"]),
        st.text(max_size=50),
    ),
    st.text(min_size=1000, max_size=10000),
    st.just("\ufeff" * 10),
    st.just("\u200b" * 100),
    st.just("\u202e" + "evil" + "\u202c"),
    st.just("\ud800"),
    st.just("\udfff"),
    st.just("A\u0300" * 100),
    st.text(alphabet="\u200b\u200c\u200d\u2060\ufeff", max_size=200),
    st.builds(
        lambda a, b: f"=?UTF-8?Q?{a}?= =?ISO-8859-1?B?{b}?=",
        st.text(max_size=30),
        st.text(max_size=30),
    ),
    st.just('"\\"\\"\\"" <test@test.com>'),
    st.builds(
        lambda c: f"(comment {c}) name <test@test.com>",
        st.text(max_size=100),
    ),
    st.builds(
        lambda n: "(" * n + "nested" + ")" * n,
        st.integers(min_value=0, max_value=50),
    ),
    st.just("user@[127.0.0.1]"),
    st.just("user@[IPv6:::1]"),
    st.builds(
        lambda l, d: f"{l}@{d}",
        st.text(min_size=100, max_size=500),
        st.text(min_size=100, max_size=500),
    ),
    st.just("test\x00@example.com"),
    st.just("test@exam\r\nple.com"),
    st.builds(
        lambda n: "\\" * n + "test@example.com",
        st.integers(min_value=0, max_value=50),
    ),
)

# MIME-specific strategies
mime_types = st.sampled_from(
    [
        "text/plain",
        "text/html",
        "text/csv",
        "text/calendar",
        "application/octet-stream",
        "application/pdf",
        "application/json",
        "application/xml",
        "application/zip",
        "application/gzip",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "audio/mpeg",
        "audio/wav",
        "video/mp4",
        "message/rfc822",
        "message/partial",
        "message/external-body",
        "multipart/mixed",
        "multipart/alternative",
        "multipart/related",
        "multipart/digest",
        "multipart/parallel",
        "multipart/report",
        # Malformed/unusual types
        "",
        "invalid",
        "/",
        "text/",
        "/plain",
        "text//plain",
        "x-custom/x-type",
        "APPLICATION/OCTET-STREAM",
    ]
)

charsets = st.sampled_from(
    [
        "utf-8",
        "UTF-8",
        "iso-8859-1",
        "ISO-8859-1",
        "us-ascii",
        "ASCII",
        "windows-1252",
        "gb2312",
        "big5",
        "euc-jp",
        "shift_jis",
        "koi8-r",
        # Malformed/unknown
        "",
        "invalid-charset",
        "utf8",
        "utf_8",
        "UTF8",
        "UNKNOWN",
    ]
)

transfer_encodings = st.sampled_from(
    [
        "7bit",
        "8bit",
        "binary",
        "quoted-printable",
        "base64",
        "7BIT",
        "BASE64",
        "QUOTED-PRINTABLE",
        # Invalid
        "",
        "invalid",
        "gzip",
        "deflate",
        "chunked",
    ]
)

boundary = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-=",
    min_size=1,
    max_size=70,
)

filename_strategy = st.one_of(
    st.text(max_size=100),
    st.just("../../../etc/passwd"),
    st.just("..\\..\\..\\windows\\system32\\config\\sam"),
    st.just("file\x00.txt"),
    st.just("file\r\n.txt"),
    st.just("." * 500 + ".txt"),
    st.just("CON.txt"),
    st.just("NUL"),
    st.just("file<script>.txt"),
    st.builds(lambda n: "a" * n + ".txt", st.integers(min_value=1, max_value=500)),
)


@pytest.mark.fuzz
class TestSimpleMessageFuzzing:
    """Fuzz tests for simple email message parsing."""

    @given(
        from_addr=email_with_name,
        to_addr=chaotic_address,
        subject=st.text(max_size=500),
        body=st.text(max_size=5000),
    )
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_message_structured(self, from_addr, to_addr, subject, body):
        """parse_email_message should handle structured but fuzzy emails."""
        raw_email = f"""From: {from_addr}
To: {to_addr}
Subject: {subject}
Date: Mon, 1 Jan 2024 12:00:00 +0000
Message-ID: <test@example.com>

{body}
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        if result is not None:
            assert "from" in result
            assert "to" in result
            assert "subject" in result
            assert isinstance(result["from"], dict)
            assert "name" in result["from"]
            assert "email" in result["from"]

    @given(data=st.binary(max_size=50000))
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_message_random_bytes(self, data):
        """parse_email_message should not crash on random bytes."""
        try:
            result = parse_email_message(data)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        from_addr=evil_text,
        to_addr=evil_text,
        subject=evil_text,
        body=evil_text,
    )
    @settings(**FUZZ_SETTINGS)
    def test_parse_email_message_evil_headers(self, from_addr, to_addr, subject, body):
        """parse_email_message should handle evil header values."""
        raw_email = f"""From: {from_addr}
To: {to_addr}
Subject: {subject}
Date: Mon, 1 Jan 2024 12:00:00 +0000

{body}
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)


@pytest.mark.fuzz
class TestCompleteEmlFuzzing:
    """Fuzz tests for complete .eml file parsing."""

    @given(
        from_addr=header_evil,
        to_addr=header_evil,
        cc=header_evil,
        bcc=header_evil,
        subject=header_evil,
        date=header_evil,
        message_id=header_evil,
        reply_to=header_evil,
        in_reply_to=header_evil,
        references=header_evil,
        content_type=mime_types,
        charset=charsets,
        transfer_encoding=transfer_encodings,
        body=evil_text,
    )
    @settings(**FUZZ_SETTINGS)
    def test_single_part_eml_all_headers(  # pylint: disable=too-many-arguments
        self,
        from_addr,
        to_addr,
        cc,
        bcc,
        subject,
        date,
        message_id,
        reply_to,
        in_reply_to,
        references,
        content_type,
        charset,
        transfer_encoding,
        body,
    ):
        """Test single-part emails with all possible headers fuzzed."""
        raw_email = f"""From: {from_addr}
To: {to_addr}
Cc: {cc}
Bcc: {bcc}
Subject: {subject}
Date: {date}
Message-ID: {message_id}
Reply-To: {reply_to}
In-Reply-To: {in_reply_to}
References: {references}
MIME-Version: 1.0
Content-Type: {content_type}; charset="{charset}"
Content-Transfer-Encoding: {transfer_encoding}

{body}
""".encode("utf-8", errors="replace")

        try:
            result = parse_email_message(raw_email)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        boundary1=boundary,
        text_body=evil_text,
        html_body=evil_text,
        charset1=charsets,
        charset2=charsets,
        encoding1=transfer_encodings,
        encoding2=transfer_encodings,
    )
    @settings(**FUZZ_SETTINGS)
    def test_multipart_alternative_eml(
        self, boundary1, text_body, html_body, charset1, charset2, encoding1, encoding2
    ):
        """Test multipart/alternative emails."""
        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Multipart Test
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="{boundary1}"

--{boundary1}
Content-Type: text/plain; charset="{charset1}"
Content-Transfer-Encoding: {encoding1}

{text_body}
--{boundary1}
Content-Type: text/html; charset="{charset2}"
Content-Transfer-Encoding: {encoding2}

{html_body}
--{boundary1}--
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(
        boundary1=boundary,
        body=evil_text,
        attachment_data=st.binary(max_size=5000),
        attachment_name=filename_strategy,
        attachment_type=mime_types,
    )
    @settings(**FUZZ_SETTINGS)
    def test_multipart_mixed_with_attachment(
        self, boundary1, body, attachment_data, attachment_name, attachment_type
    ):
        """Test multipart/mixed emails with attachments."""
        attachment_b64 = base64.b64encode(attachment_data).decode("ascii")

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Email with Attachment
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary1}"

--{boundary1}
Content-Type: text/plain; charset="utf-8"

{body}
--{boundary1}
Content-Type: {attachment_type}; name="{attachment_name}"
Content-Disposition: attachment; filename="{attachment_name}"
Content-Transfer-Encoding: base64

{attachment_b64}
--{boundary1}--
""".encode("utf-8", errors="replace")

        try:
            result = parse_email_message(raw_email)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        depth=st.integers(min_value=1, max_value=10),
        boundaries=st.lists(boundary, min_size=11, max_size=11),
        body=evil_text,
    )
    @settings(
        max_examples=1000,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_deeply_nested_multipart(self, depth, boundaries, body):
        """Test deeply nested multipart structures."""
        parts = []
        for i in range(depth):
            parts.append(f"""--{boundaries[i]}
Content-Type: multipart/mixed; boundary="{boundaries[i + 1]}"

""")

        parts.append(f"""--{boundaries[depth]}
Content-Type: text/plain

{body}
--{boundaries[depth]}--
""")

        for i in range(depth - 1, -1, -1):
            parts.append(f"""
--{boundaries[i]}--
""")

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Nested Email
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundaries[0]}"

{"".join(parts)}
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(
        boundary_declared=boundary,
        boundary_used=boundary,
        body=st.text(max_size=1000),
    )
    @settings(**FUZZ_SETTINGS)
    def test_mismatched_boundaries(self, boundary_declared, boundary_used, body):
        """Test emails where declared boundary doesn't match used boundary."""
        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Mismatched Boundary
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary_declared}"

--{boundary_used}
Content-Type: text/plain

{body}
--{boundary_used}--
""".encode("utf-8", errors="replace")

        try:
            result = parse_email_message(raw_email)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        num_parts=st.integers(min_value=0, max_value=100),
        boundary_str=boundary,
    )
    @settings(max_examples=500, deadline=None)
    def test_many_mime_parts(self, num_parts, boundary_str):
        """Test emails with many MIME parts."""
        parts = []
        for i in range(num_parts):
            parts.append(f"""--{boundary_str}
Content-Type: text/plain

Part {i}
""")

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Many Parts
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary_str}"

{"".join(parts)}--{boundary_str}--
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(
        body=st.binary(max_size=10000),
        declared_encoding=transfer_encodings,
    )
    @settings(**FUZZ_SETTINGS)
    def test_binary_body_with_text_encoding(self, body, declared_encoding):
        """Test binary data claimed as text with various encodings."""
        header = f"""From: test@example.com
To: recipient@example.com
Subject: Binary as Text
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: {declared_encoding}

""".encode("utf-8")

        raw_email = header + body

        try:
            result = parse_email_message(raw_email)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        inline_content=st.binary(max_size=5000),
        content_id=st.text(max_size=100),
        inline_type=st.sampled_from(["image/png", "image/jpeg", "image/gif"]),
    )
    @settings(**FUZZ_SETTINGS)
    def test_inline_attachments_cid(self, inline_content, content_id, inline_type):
        """Test emails with inline attachments (Content-ID)."""
        content_b64 = base64.b64encode(inline_content).decode("ascii")

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Inline Image
MIME-Version: 1.0
Content-Type: multipart/related; boundary="related-boundary"

--related-boundary
Content-Type: text/html; charset="utf-8"

<html><body><img src="cid:{content_id}"></body></html>
--related-boundary
Content-Type: {inline_type}
Content-ID: <{content_id}>
Content-Transfer-Encoding: base64
Content-Disposition: inline

{content_b64}
--related-boundary--
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(
        header_count=st.integers(min_value=0, max_value=200),
        header_name=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-",
            min_size=1,
            max_size=50,
        ),
        header_value=st.text(max_size=500),
    )
    @settings(max_examples=1000, deadline=None)
    def test_many_headers(self, header_count, header_name, header_value):
        """Test emails with many headers."""
        headers = "\n".join(
            [f"X-Custom-{header_name}-{i}: {header_value}" for i in range(header_count)]
        )

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Many Headers
{headers}

Body
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(
        line_length=st.integers(min_value=1, max_value=10000),
    )
    @settings(max_examples=500, deadline=None)
    def test_very_long_header_lines(self, line_length):
        """Test emails with very long header lines (no folding)."""
        long_subject = "A" * line_length

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: {long_subject}

Body
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(
        fold_count=st.integers(min_value=1, max_value=100),
        fold_char=st.sampled_from([" ", "\t", "  ", "\t\t", " \t", "\t "]),
    )
    @settings(max_examples=1000, deadline=None)
    def test_header_folding_variations(self, fold_count, fold_char):
        """Test various header folding patterns."""
        parts = ["Part"] * fold_count
        folded_subject = f"\r\n{fold_char}".join(parts)

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: {folded_subject}

Body
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

    @given(data=st.binary(max_size=100000))
    @settings(**FUZZ_SETTINGS)
    def test_completely_random_binary(self, data):
        """Test completely random binary data as email."""
        try:
            result = parse_email_message(data)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        prefix=st.binary(max_size=1000),
        suffix=st.binary(max_size=1000),
    )
    @settings(**FUZZ_SETTINGS)
    def test_valid_email_with_binary_noise(self, prefix, suffix):
        """Test valid email structure surrounded by binary noise."""
        valid_email = b"""From: test@example.com
To: recipient@example.com
Subject: Test

Body content here.
"""
        raw_email = prefix + valid_email + suffix

        try:
            result = parse_email_message(raw_email)
            assert result is None or isinstance(result, dict)
        except EmailParseError:
            pass  # Expected for malformed input

    @given(
        embedded_eml=st.binary(max_size=5000),
    )
    @settings(**FUZZ_SETTINGS)
    def test_message_rfc822_attachment(self, embedded_eml):
        """Test emails with message/rfc822 attachments (forwarded emails)."""
        embedded_b64 = base64.b64encode(embedded_eml).decode("ascii")

        raw_email = f"""From: test@example.com
To: recipient@example.com
Subject: Forwarded Email
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="outer-boundary"

--outer-boundary
Content-Type: text/plain

See the forwarded message below.
--outer-boundary
Content-Type: message/rfc822
Content-Disposition: attachment; filename="forwarded.eml"
Content-Transfer-Encoding: base64

{embedded_b64}
--outer-boundary--
""".encode("utf-8", errors="replace")

        result = parse_email_message(raw_email)
        assert result is None or isinstance(result, dict)

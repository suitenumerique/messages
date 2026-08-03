"""Tests for the parser-ambiguity defects surfaced in ``_ext.defects``.

Each construct below is one a spam filter, a virus scanner and a mail
client can legitimately resolve differently. We resolve them the way the
stdlib does; these markers exist so a consumer running a quarantine or
scanning policy can see that a choice was made at all.

Motivated by "Email Smuggling with Differential Fuzzing of MIME Parsers"
(Andarzian, Meyers & Poll, 2025), which demonstrates payloads smuggled
past filters on exactly these constructs.
"""

import pytest

from jmap_email import compose_email, parse_email


def defects_of(raw: bytes) -> list[str]:
    parsed = parse_email(raw, extensions=True)
    assert parsed is not None
    return (parsed.get("_ext") or {}).get("defects") or []


CLEAN = (
    b"From: a@b.co\n"
    b"Subject: t\n"
    b"MIME-Version: 1.0\n"
    b'Content-Type: multipart/mixed; boundary="----=_x"\n'
    b"\n"
    b"------=_x\n"
    b"Content-Type: text/plain\n"
    b"Content-Transfer-Encoding: 7bit\n"
    b"\n"
    b"hello\n"
    b"------=_x--\n"
)


def test_clean_message_raises_no_ambiguity_defect():
    """The markers must be quiet on well-formed mail, or they're noise."""
    assert defects_of(CLEAN) == []


@pytest.mark.parametrize(
    "encoding",
    [
        pytest.param(b"7bit", id="7bit"),
        pytest.param(b"8bit", id="8bit"),
        pytest.param(b"binary", id="binary"),
        pytest.param(b"base64", id="base64"),
        pytest.param(b"quoted-printable", id="quoted-printable"),
        pytest.param(b"BASE64", id="uppercase"),
        pytest.param(b"  base64  ", id="padded"),
    ],
)
def test_known_transfer_encodings_are_not_flagged(encoding):
    raw = (
        b"From: a@b.co\nSubject: t\nContent-Type: text/plain\n"
        b"Content-Transfer-Encoding: " + encoding + b"\n\nx\n"
    )
    assert "UnrecognizedTransferEncodingDefect" not in defects_of(raw)


def test_duplicate_content_type():
    """We take the first; clients have been seen honouring the second,
    which turns a flat text body into a multipart tree whose attachments
    we never extract."""
    raw = (
        b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
        b"Content-Type: text/plain\n"
        b'Content-Type: multipart/mixed; boundary="----=_x"\n'
        b"\n------=_x\n\nsecond\n------=_x--\n"
    )
    assert "DuplicateContentTypeDefect" in defects_of(raw)


def test_duplicate_transfer_encoding():
    """The paper's D3: base64 first, 7bit second smuggled past a filter
    that took the second while the clients took the first."""
    raw = (
        b"From: a@b.co\nSubject: t\nContent-Type: text/plain\n"
        b"Content-Transfer-Encoding: base64\n"
        b"Content-Transfer-Encoding: 7bit\n\nU01VR0dMRUQ=\n"
    )
    assert "DuplicateTransferEncodingDefect" in defects_of(raw)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            b"From: a@b.co\nSubject: t\nContent-Type: text/plain\n"
            b"Content-Transfer-Encoding: bas64\n\nU01VR0dMRUQ=\n",
            id="near-miss-token",
        ),
        pytest.param(
            b"From: a@b.co\nSubject: t\nContent-Type: text/plain\n"
            b"Content-Transfer-Encoding:: base64\n\nU01VR0dMRUQ=\n",
            id="extra-colon",
        ),
    ],
)
def test_unrecognized_transfer_encoding(raw):
    """We leave the body undecoded. Lenient clients guess — ClamAV decodes
    anything base64-ish, Evolution parses past the extra colon — so the
    payload is visible to them and not to a scanner reading our output."""
    assert "UnrecognizedTransferEncodingDefect" in defects_of(raw)


def test_non_empty_preamble():
    """RFC 2046 §5.1.1 says ignore the preamble, and we do. Thunderbird
    and Evolution render it as the message's first line."""
    raw = (
        b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
        b'Content-Type: multipart/mixed; boundary="----=_x"\n'
        b"\nSMUGGLED\n------=_x\nContent-Type: text/plain\n\nvisible\n------=_x--\n"
    )
    assert "NonEmptyPreambleDefect" in defects_of(raw)
    # And the smuggled text really is absent from the parsed output.
    parsed = parse_email(raw)
    assert parsed is not None
    assert "SMUGGLED" not in str(parsed["textBody"])


def test_whitespace_only_preamble_is_not_flagged():
    raw = (
        b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
        b'Content-Type: multipart/mixed; boundary="----=_x"\n'
        b"\n   \n------=_x\nContent-Type: text/plain\n\nvisible\n------=_x--\n"
    )
    assert "NonEmptyPreambleDefect" not in defects_of(raw)


def test_defects_absent_without_extensions():
    """The markers ride on the opt-in extension namespace only."""
    parsed = parse_email(CLEAN)
    assert parsed is not None
    assert "_ext" not in parsed


class TestHeaderAmbiguity:
    """Root-level duplications. RFC 5322 §3.6 caps these at one each."""

    def test_duplicate_from_is_called_out_separately(self):
        """A second ``From`` shows one identity to the filter that
        authenticated the message and another to the human reading it —
        CERT VU#517845, and "Weak Links in Authentication Chains"
        (USENIX 2020), which is already in the defense matrix."""
        raw = (
            b"From: alice@good.co\nFrom: mallory@evil.co\n"
            b"Subject: t\nMIME-Version: 1.0\nContent-Type: text/plain\n\nx\n"
        )
        assert "DuplicateFromDefect" in defects_of(raw)

    @pytest.mark.parametrize(
        "header",
        [b"Subject", b"Date", b"Message-ID", b"To", b"Reply-To", b"References"],
    )
    def test_duplicate_singleton_headers(self, header):
        raw = (
            b"From: a@b.co\nMIME-Version: 1.0\nContent-Type: text/plain\n"
            + header
            + b": one\n"
            + header
            + b": two\n\nx\n"
        )
        assert "DuplicateScalarHeaderDefect" in defects_of(raw)

    def test_nested_message_from_is_not_flagged(self):
        """An attached ``message/rfc822`` legitimately carries its own
        ``From``; flagging every forwarded email would be noise."""
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            b"Content-Type: message/rfc822\n\n"
            b"From: inner@c.co\nSubject: inner\n\ninner body\n--x--\n"
        )
        assert "DuplicateFromDefect" not in defects_of(raw)


class TestStructuralAmbiguity:
    def test_non_empty_epilogue(self):
        """The preamble's mirror image: RFC 2046 §5.1 says ignore both."""
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            b"Content-Type: text/plain\n\nvisible\n--x--\nSMUGGLED\n"
        )
        assert "NonEmptyEpilogueDefect" in defects_of(raw)

    def test_duplicate_boundary_parameter(self):
        """Whichever boundary we honour, the other delimits parts we never
        see (Inbox Invasion, CCS '24)."""
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"; boundary="y"\n\n--x\n'
            b"Content-Type: text/plain\n\nfrom-x\n--x--\n"
        )
        assert "DuplicateBoundaryParameterDefect" in defects_of(raw)

    def test_missing_mime_version(self):
        """MIME syntax with nothing licensing it: a strict receiver reads
        the body as flat text and never sees the parts."""
        raw = (
            b"From: a@b.co\nSubject: t\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            b"Content-Type: text/plain\n\nhello\n--x--\n"
        )
        assert "MissingMimeVersionDefect" in defects_of(raw)


class TestAttachmentNameAmbiguity:
    """A part that names itself twice, differently."""

    def _one_part(self, headers: bytes) -> bytes:
        return (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            + headers
            + b"\nAAA\n--x--\n"
        )

    def test_content_type_name_disagrees_with_filename(self):
        raw = self._one_part(
            b'Content-Type: application/octet-stream; name="evil.exe"\n'
            b'Content-Disposition: attachment; filename="safe.txt"\n'
        )
        assert "ConflictingAttachmentNameDefect" in defects_of(raw)

    def test_filename_star_wins_per_rfc6266(self):
        """RFC 6266 §4.3: with both present, recipients SHOULD take
        ``filename*``. The stdlib's ``get_filename`` returns the plain
        one, so a sender could show us ``safe.txt`` while every
        spec-following client saved ``evil.exe``."""
        raw = self._one_part(
            b"Content-Type: application/octet-stream\n"
            b"Content-Disposition: attachment; "
            b"filename=\"safe.txt\"; filename*=UTF-8''evil.exe\n"
        )
        parsed = parse_email(raw)
        assert parsed is not None
        assert [a["name"] for a in parsed["attachments"]] == ["evil.exe"]
        assert "ConflictingAttachmentNameDefect" in defects_of(raw)

    def test_agreeing_names_are_not_flagged(self):
        raw = self._one_part(
            b'Content-Type: application/pdf; name="r.pdf"\n'
            b'Content-Disposition: attachment; filename="r.pdf"\n'
        )
        assert "ConflictingAttachmentNameDefect" not in defects_of(raw)


class TestDecodeTimeDefectsAreCollected:
    """Regression: the stdlib attaches these while *decoding* a payload,
    and the collection walk used to run before any decoding happened, so
    every one of them was silently dropped from ``_ext.defects``."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            pytest.param(
                b"U01VR0!!dMRUQ=", "InvalidBase64CharactersDefect", id="chars"
            ),
            pytest.param(b"U01VR0dMRUQ", "InvalidBase64PaddingDefect", id="padding"),
        ],
    )
    def test_base64_decode_defects_reach_ext(self, payload, expected):
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b"Content-Type: text/plain\nContent-Transfer-Encoding: base64\n\n"
            + payload
            + b"\n"
        )
        assert expected in defects_of(raw)


class TestIetfDraftAnomalyClasses:
    """The classes named by draft-chen-email-mime-ambiguity-defense.

    That draft enumerates the constructs an ingress filter should treat
    as ambiguous rather than resolve silently. These are the ones the
    stdlib does not already flag for us.
    """

    def test_control_char_in_mime_header(self):
        """A NUL in a filename is read three ways: stripped (us),
        truncated at the NUL, or kept. Same part, three names."""
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            b"Content-Type: application/octet-stream\n"
            b'Content-Disposition: attachment; filename="report.pdf\x00.exe"\n'
            b"\nAAA\n--x--\n"
        )
        assert "ControlCharInHeaderDefect" in defects_of(raw)

    @pytest.mark.parametrize(
        "content_type",
        [
            pytest.param(b'multipart/mixed; boundary=""', id="empty"),
            pytest.param(b"multipart/mixed", id="absent"),
        ],
    )
    def test_empty_or_missing_boundary(self, content_type):
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\nContent-Type: "
            + content_type
            + b"\n\n--x\nContent-Type: text/plain\n\nhi\n--x--\n"
        )
        assert "EmptyBoundaryDefect" in defects_of(raw)

    def test_encoded_word_in_parameter(self):
        """RFC 2231 is the mechanism for non-ASCII parameters; RFC 2047
        encoded-words are not permitted there. We decode them, so a
        scanner that doesn't sees a different filename than the user."""
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            b"Content-Type: application/pdf\n"
            b'Content-Disposition: attachment; filename="=?utf-8?B?ZXZpbC5leGU=?="\n'
            b"\nAAA\n--x--\n"
        )
        assert "EncodedWordInParameterDefect" in defects_of(raw)
        parsed = parse_email(raw)
        assert parsed is not None
        assert [a["name"] for a in parsed["attachments"]] == ["evil.exe"]

    def test_rfc2231_parameter_is_not_flagged(self):
        """The correct mechanism must not trip the marker."""
        raw = (
            b"From: a@b.co\nSubject: t\nMIME-Version: 1.0\n"
            b'Content-Type: multipart/mixed; boundary="x"\n\n--x\n'
            b"Content-Type: application/pdf\n"
            b"Content-Disposition: attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf\n"
            b"\nAAA\n--x--\n"
        )
        assert "EncodedWordInParameterDefect" not in defects_of(raw)


class TestOpaqueAndAmbiguousPartMarkers:
    """Markers for parts we hand over without looking inside, and for the
    one fold that changes an attachment's name.

    Motivated by *Inbox Invasion* (CCS '24) and the nested-RFC822 /
    CRLF-filename technique reported in the wild: the payload is placed
    where the reader's parser finds it and the scanner's does not.
    """

    @staticmethod
    def _wrap(part_headers: bytes, body: bytes = b"MZ") -> bytes:
        return (
            b"From: o@x.co\r\nTo: a@b.co\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\n"
            + part_headers
            + b"\r\n\r\n"
            + body
            + b"\r\n--B--\r\n"
        )

    @staticmethod
    def _defects(raw: bytes) -> set[str]:
        parsed = parse_email(raw, extensions=True)
        assert parsed is not None
        return set((parsed.get("_ext") or {}).get("defects") or [])

    def test_fold_inside_a_quoted_filename_is_flagged(self):
        """``filename="pay<CRLF> load.exe"`` is read three ways: we unfold
        to ``pay load.exe`` per RFC 5322 §2.2.3, a parser dropping the
        whole fold sees ``payload.exe``, one truncating at CR sees
        ``pay``. Legal syntax, three names, one attachment."""
        raw = self._wrap(
            b"Content-Type: application/octet-stream\r\n"
            b'Content-Disposition: attachment; filename="pay\r\n load.exe"'
        )
        assert "FoldInQuotedParameterDefect" in self._defects(raw)
        parsed = parse_email(raw)
        assert parsed["attachments"][0]["name"] == "pay load.exe"

    def test_ordinary_folding_between_parameters_is_not_flagged(self):
        """Folding is how long headers are written; only folding *inside*
        the quotes is ambiguous. Flagging both would be noise."""
        raw = self._wrap(
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment;\r\n"
            b' filename="report.pdf"'
        )
        assert "FoldInQuotedParameterDefect" not in self._defects(raw)

    def test_message_partial_is_flagged(self):
        """RFC 2046 §5.2.2 splits one message across several, so the
        payload exists only after a reassembly a per-message scanner
        never performs."""
        raw = (
            b"From: o@x.co\r\nTo: a@b.co\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
            b'Content-Type: message/partial; id="x@y"; number=1; total=2\r\n\r\n'
            b"From: inner@x.co\r\nSubject: half\r\n\r\nfragment\r\n"
        )
        assert "PartialMessageDefect" in self._defects(raw)

    def test_message_external_body_is_flagged(self):
        """The content is fetched from elsewhere, so it is not in this
        message for anyone to scan."""
        raw = self._wrap(
            b"Content-Type: message/external-body; access-type=URL;"
            b' URL="http://evil.co/p.exe"',
            body=b"Content-Type: application/octet-stream\r\n\r\n",
        )
        assert "ExternalBodyDefect" in self._defects(raw)

    def test_nested_rfc822_is_deliberately_not_flagged(self):
        """The documented scope boundary: a marker here would fire on
        every forwarded message, so it would be noise rather than signal.
        Pinned so the decision is visible rather than accidental — the
        nested payload is still reachable by re-parsing ``content``."""
        inner = b"From: i@x.co\r\nSubject: inner\r\n\r\nbody\r\n"
        raw = self._wrap(
            b"Content-Type: message/rfc822\r\nContent-Disposition: attachment",
            body=inner,
        )
        defects = self._defects(raw)
        assert "PartialMessageDefect" not in defects
        assert "ExternalBodyDefect" not in defects
        # And the bytes are still there to recurse into.
        parsed = parse_email(raw)
        nested = parsed["attachments"][0]
        assert nested["type"] == "message/rfc822"
        assert parse_email(nested["content"])["subject"] == "inner"

    def test_ordinary_attachment_raises_no_marker(self):
        """The markers are only worth anything if normal mail is clean."""
        raw = self._wrap(
            b"Content-Type: application/pdf\r\n"
            b'Content-Disposition: attachment; filename="report.pdf"'
        )
        assert self._defects(raw) == set()


class TestStdlibEmailCveRegressions:
    """Behaviour we inherit from CPython, pinned because the 3.14.6 floor
    exists precisely to carry ``email`` fixes — a downgrade or a vendored
    stdlib would reintroduce these silently."""

    def test_cve_2025_1795_address_list_folding_keeps_its_commas(self):
        """A comma separator landing on a folded, unicode-encoded line was
        itself RFC 2047-encoded, so receivers merged or split recipients.
        The invariant is arithmetic: what goes in comes out."""
        recipients = [
            {
                "name": f"Zoé Ünicode Nom Très Long Numéro {i}",
                "email": f"r{i}@example.com",
            }
            for i in range(8)
        ]
        raw = compose_email(
            {
                "from": [{"name": "Émetteur", "email": "s@e.co"}],
                "to": recipients,
                "subject": "sujet",
                "sentAt": "2026-01-01T00:00:00+00:00",
                "textBody": [{"content": "b"}],
            }
        )
        # The header must actually fold, or the test proves nothing.
        to_header = raw.decode().split("\r\nTo: ")[1].split("\r\nSubject")[0]
        assert "\r\n" in to_header
        recovered = parse_email(raw).get("to") or []
        assert [a["email"] for a in recovered] == [r["email"] for r in recipients]

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param("evil\r\nBcc: x@y.co", id="crlf"),
            pytest.param("evil\nBcc: x@y.co", id="lf"),
            pytest.param("evil\rBcc: x@y.co", id="cr"),
        ],
    )
    def test_cve_2024_6923_newline_in_header_cannot_inject(self, payload):
        """The stdlib failed to quote newlines in header values. We strip
        them before they reach the header machinery, so neither layer
        alone is load-bearing."""
        raw = compose_email(
            {
                "from": [{"name": payload, "email": "s@e.co"}],
                "to": [{"name": None, "email": "a@b.co"}],
                "subject": payload,
                "sentAt": "2026-01-01T00:00:00+00:00",
                "textBody": [{"content": "b"}],
            }
        )
        names = {h["name"].lower() for h in (parse_email(raw).get("headers") or [])}
        assert "bcc" not in names

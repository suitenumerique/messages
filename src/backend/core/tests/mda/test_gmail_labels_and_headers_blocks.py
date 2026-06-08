"""Tests for the Messages-specific computations in
``core.mda.utils`` — Gmail-style label extraction and Received-
bounded header-block grouping.

Both functions consume a ``parsed_email`` dict and walk its
``headers`` list. They do not depend on the parser's ``ext`` namespace,
so they work on any output of :func:`jmap_email.parse_email`.
"""

import pytest
from jmap_email import parse_email

from core.mda.utils import gmail_labels, headers_blocks


class TestGmailLabels:
    """Label extraction from ``X-Gmail-Labels`` / ``X-Keywords``."""

    @staticmethod
    def _parse(label_header_value: str, header_name: str = "X-Gmail-Labels"):
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            + header_name.encode("ascii")
            + b": "
            + label_header_value.encode("utf-8")
            + b"\r\n\r\nbody\r\n"
        )
        return parse_email(raw)

    def test_simple_comma_separated(self):
        """Plain comma-separated input is the OfflineIMAP convention."""
        parsed = self._parse("Important, Work, Personal")
        assert gmail_labels(parsed) == ["Important", "Work", "Personal"]

    def test_quoted_with_comma_inside(self):
        """A quoted label may itself contain a comma — quoting wins."""
        parsed = self._parse('"Culture, associations, événements"')
        assert gmail_labels(parsed) == ["Culture, associations, événements"]

    def test_dovecot_space_separated(self):
        """When no comma is present, fall back to shlex-style space split."""
        parsed = self._parse("work important project", header_name="X-Keywords")
        assert gmail_labels(parsed) == ["work", "important", "project"]

    def test_empty_quoted_strings_are_dropped(self):
        """Empty quoted entries are filtered out — only real labels survive."""
        parsed = self._parse('"", Work, ""')
        labels = gmail_labels(parsed)
        assert "Work" in labels
        assert "" not in labels

    def test_combined_x_gmail_labels_and_x_keywords_dedup(self):
        """Both headers contribute to one deduped list, first-seen order."""
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b"X-Gmail-Labels: gmail-only, shared\r\n"
            b"X-Keywords: keywords-only, shared\r\n"
            b"\r\nbody\r\n"
        )
        parsed = parse_email(raw)
        labels = gmail_labels(parsed)
        assert labels.count("shared") == 1
        assert "gmail-only" in labels
        assert "keywords-only" in labels

    def test_no_labels_header_returns_empty(self):
        """No ``X-Gmail-Labels`` or ``X-Keywords`` → empty list, never KeyError."""
        raw = b"From: a@b.c\r\nTo: d@e.f\r\nSubject: hi\r\n\r\nbody\r\n"
        assert not gmail_labels(parse_email(raw))

    def test_empty_parsed_email(self):
        """A defensive null-safety check — accept ``{}``, return ``[]``."""
        assert not gmail_labels({})

    def test_utf8_encoded_labels_preserve_nbsp(self):
        """UTF-8 encoded RFC 2047 labels survive intact, including
        embedded NBSP (U+00A0)."""
        raw = (
            b"From: a@b.c\r\nTo: d@e.f\r\n"
            b"X-Gmail-Labels: =?UTF-8?Q?Messages_archiv=C3=A9s,Ouvert,Cat=C3=A9gorie=C2=A0:_E-mails_?=\r\n"
            b' =?UTF-8?Q?personnels,"Culture,_associations,_=C3=A9v=C3=A9nements"?=\r\n'
            b"\r\nbody\r\n"
        )
        labels = gmail_labels(parse_email(raw))
        assert "Messages archivés" in labels
        assert "Ouvert" in labels
        assert "Catégorie\xa0: E-mails personnels" in labels
        assert "Culture, associations, événements" in labels


class TestHeadersBlocks:
    """Received-bounded trust scope grouping."""

    def test_no_received_yields_one_block(self):
        """A message with zero Received headers collapses to a single block."""
        raw = b"From: a@b.c\r\nTo: d@e.f\r\nSubject: hi\r\n\r\nbody\r\n"
        blocks = headers_blocks(parse_email(raw))
        assert len(blocks) == 1
        assert "from" in blocks[0]
        assert "to" in blocks[0]
        assert "subject" in blocks[0]

    def test_received_marks_block_boundary(self):
        """A Received header marks the END of its block. With one
        Received in the middle, we get 2 blocks: the headers ABOVE +
        Received itself, then the final headers."""
        raw = (
            b"Received: from hop1 by hop2\r\n"
            b"X-Spam: Ham\r\n"
            b"From: a@b.c\r\nTo: d@e.f\r\nSubject: hi\r\n\r\nbody\r\n"
        )
        blocks = headers_blocks(parse_email(raw))
        assert len(blocks) == 2
        # Block 0 ends at the Received and includes it.
        assert "received" in blocks[0]
        # Block 1 carries the final headers (no Received).
        assert "from" in blocks[1]
        assert "to" in blocks[1]

    def test_every_value_is_a_list(self):
        """Block values are always ``list[str]`` for uniform indexing —
        even scalar (max=1) headers like Subject."""
        raw = (
            b"Received: from hop1 by hop2\r\n"
            b"From: a@b.c\r\nSubject: scalar in block\r\n\r\nbody\r\n"
        )
        for block in headers_blocks(parse_email(raw)):
            for value in block.values():
                assert isinstance(value, list)
                assert all(isinstance(v, str) for v in value)

    def test_multiple_received_headers_split_into_multiple_blocks(self):
        """N Received headers → N closing blocks plus a final residual block."""
        raw = (
            b"Received: by our_mta\r\n"
            b"X-Spam: ham\r\n"
            b"Received: by relay2\r\n"
            b"X-Spam: spam\r\n"
            b"Received: by relay1\r\n"
            b"From: a@b.c\r\nSubject: t\r\n\r\nbody\r\n"
        )
        blocks = headers_blocks(parse_email(raw))
        # 3 Received headers → 3 closing blocks + 1 final block.
        assert len(blocks) == 4

    def test_empty_parsed_email(self):
        """Defensive null-safety: ``{}`` is accepted and returns ``[]``."""
        assert not headers_blocks({})


if __name__ == "__main__":
    pytest.main()

"""Inputs that have crashed or stalled other MIME parsers.

Each payload below is taken from a published defect in another parser
(CPython's ``email`` package, Dovecot, Haraka, Stalwart, Exim, Apache
James). The inbound MTA forwards message bytes untouched, so this parser is
the first thing to read them: whatever a sender puts in a message, parsing
has to end, in bounded time, with a parsed message or ``None``.

A message that raised here instead would be a poison message: the MDA
answers 5xx, the MTA defers, and the sender redelivers the same bytes until
its queue expires.
"""

import signal
import sys
import time

import pytest

from jmap_email import compose_email, parse_email

# Generous for a CI machine, and still far below "pins a worker".
TIME_LIMIT = 20.0

HEAD = b"From: a@example.com\r\nTo: b@example.com\r\nSubject: s\r\n"


def _nested_multipart(depth: int) -> bytes:
    """``depth`` multipart/mixed parts, each inside the previous one."""
    out = [HEAD]
    for level in range(depth):
        out.append(
            f'Content-Type: multipart/mixed; boundary="b{level}"\r\n\r\n--b{level}\r\n'.encode()
        )
    out.append(b"Content-Type: text/plain\r\n\r\ndeep\r\n")
    for level in reversed(range(depth)):
        out.append(f"--b{level}--\r\n".encode())
    return b"".join(out)


def _nested_rfc822(depth: int) -> bytes:
    """``depth`` message/rfc822 parts, each holding the next."""
    return (
        HEAD
        + b"Content-Type: message/rfc822\r\n\r\n" * depth
        + b"Subject: x\r\n\r\nbody\r\n"
    )


def _siblings(count: int) -> bytes:
    part = b"--b\r\nContent-Type: text/plain\r\n\r\nx\r\n"
    return (
        HEAD
        + b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
        + part * count
        + b"--b--\r\n"
    )


PAYLOADS = {
    # Dovecot CVE-2020-12100, Postfix mime_nesting_limit, CPython gh-151857:
    # RecursionError in the stdlib parser from about 500 levels.
    "nested multipart x2000": _nested_multipart(2000),
    "nested multipart x150": _nested_multipart(150),
    "nested message/rfc822 x2000": _nested_rfc822(2000),
    "nested message/rfc822 x150": _nested_rfc822(150),
    "100,000 sibling parts": _siblings(100_000),
    # Stalwart CVE-2026-26312: a part whose boundary is its parent's.
    "child reuses the parent boundary": HEAD
    + b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\n'
    + b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\n'
    + b"Content-Type: text/plain\r\n\r\nx\r\n--b--\r\n--b--\r\n",
    # CPython: IndexError on malformed RFC 2231 parameters.
    "name*= with a lone quote": HEAD
    + b'Content-Type: text/plain; name*="\'"\r\n\r\nx\r\n',
    "name*= with an empty value": HEAD
    + b"Content-Type: text/plain; name*=\"us-ascii''\"\r\n\r\nx\r\n",
    "bare name*": HEAD + b"Content-Type: text/plain; name*\r\n\r\nx\r\n",
    # Haraka GHSA-6f4j-gpc2-6p2g, CPython gh-136063: quadratic parameter parsing.
    "100 kB of RFC 2231 segments": HEAD
    + b"Content-Type: text/plain; "
    + b"; ".join(b"a*%d*=x" % i for i in range(12_000))
    + b"\r\n\r\nx\r\n",
    "10,000 filename*N parts": HEAD
    + b"Content-Disposition: attachment;\r\n "
    + b";\r\n ".join(b'filename*%d="x"' % i for i in range(10_000))
    + b"\r\n\r\nx\r\n",
    # Exim CVE-2024-39929: a file name split across continuations.
    "filename split across continuations": HEAD
    + b'Content-Disposition: attachment; filename*0="a.e"; filename*1="xe"\r\n\r\nx\r\n',
    "filename and filename* disagree": HEAD
    + b"Content-Disposition: attachment; filename=\"a.txt\"; filename*=utf-8''b.exe\r\n\r\nx\r\n",
    # CPython CVE-2023-36632 and the address-list IndexError.
    "From with 3,000 open parentheses": b"From: "
    + b"(" * 3000
    + b"\r\nSubject: s\r\n\r\nx\r\n",
    "To ending in an open quote": b'From: a@example.com\r\nTo: a@b.c, <, "\r\n\r\nx\r\n',
    # Dovecot CVE-2024-23184: quadratic on a long address list.
    "To with 70,000 addresses": b"From: a@example.com\r\nTo: "
    + b", ".join(b"u%d@example.com" % i for i in range(70_000))
    + b"\r\n\r\nx\r\n",
    "200,000 To headers": b"From: a@example.com\r\n"
    + b"To: u@example.com\r\n" * 200_000
    + b"\r\nx\r\n",
    "100,000 distinct header names": b"From: a@example.com\r\n"
    + b"".join(b"X-H%d: v\r\n" % i for i in range(100_000))
    + b"\r\nx\r\n",
    # Exim CVE-2020-28020: a header made only of continuation lines.
    "5 MB of continuation lines": b"Subject: s\r\n"
    + b" \r\n" * 1_700_000
    + b"\r\nx\r\n",
    "one 5 MB header line": b"Subject: " + b"a" * 5_000_000 + b"\r\n\r\nx\r\n",
    "100 kB of encoded-words": b"Subject: "
    + b"=?utf-8?b?YWJj?= " * 6_000
    + b"\r\n\r\nx\r\n",
    # CVE-2024-6923: an encoded-word that decodes to a line break.
    "encoded-word holding a newline": b"Subject: =?utf-8?B?Zm9vCkJjYzogeEB5?=\r\nFrom: a@example.com\r\n\r\nx\r\n",
    # Haraka CVE-2026-34752 and friends: odd header names.
    "header named __proto__": b"__proto__: x\r\n__class__: y\r\nFrom: a@example.com\r\n\r\nx\r\n",
    "empty and spaced header names": b": x\r\nFrom : a@example.com\r\nSub ject: y\r\n\r\nx\r\n",
    "two From headers": b"From: a@legit.example\r\nFrom: b@evil.example\r\n\r\nx\r\n",
    "Content-Type twice, two boundaries": HEAD
    + b'Content-Type: multipart/mixed; boundary="a"\r\n'
    + b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
    + b"--a\r\n\r\none\r\n--a--\r\n--b\r\n\r\ntwo\r\n--b--\r\n",
    "no headers, no blank line": b"just some text with no structure at all",
    "empty": b"",
    "only NUL bytes": b"\x00" * 100_000,
    "a megabyte of random-looking binary": bytes(range(256)) * 4096,
}


@pytest.mark.parametrize("name", list(PAYLOADS))
def test_parsing_ends_with_a_message_or_none(name):
    raw = PAYLOADS[name]

    fired = []

    def stalled(*_):
        fired.append(True)
        raise TimeoutError(f"still parsing {len(raw)} bytes after {TIME_LIMIT:.0f} s")

    # An alarm, so that a parser that never returns fails here instead of
    # hanging the run.
    previous = signal.signal(signal.SIGALRM, stalled)
    signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT)
    try:
        parsed = parse_email(raw)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    # Also when the parser caught the interruption as one more bad input.
    assert not fired, f"still parsing {len(raw)} bytes after {TIME_LIMIT:.0f} s"
    assert parsed is None or isinstance(parsed, dict), type(parsed)


def test_the_deadline_interrupts_a_parse_that_does_not_end(monkeypatch):
    """The guard above, checked against a loop in place of the parser."""
    monkeypatch.setitem(PAYLOADS, "endless", b"x")
    monkeypatch.setattr(sys.modules[__name__], "TIME_LIMIT", 0.2)

    def never(_raw):
        while True:
            time.sleep(0.01)

    monkeypatch.setattr(sys.modules[__name__], "parse_email", never)
    with pytest.raises(TimeoutError, match="still parsing"):
        test_parsing_ends_with_a_message_or_none("endless")


def test_a_line_break_decoded_from_a_header_cannot_be_written_back_as_one():
    """CVE-2024-6923 in the stdlib: an encoded-word that decodes to a line
    break, written back unquoted, became a header of the sender's choosing.

    The parser keeps the decoded value as it is. The composer is what makes
    it harmless, so the check is on a reply built from it.
    """
    parsed = parse_email(PAYLOADS["encoded-word holding a newline"])
    assert parsed is not None
    assert parsed["subject"] == "foo\nBcc: x@y"

    reply = compose_email(
        {
            "subject": "Re: " + parsed["subject"],
            "sentAt": "2026-11-11T10:00:00Z",
            "from": [{"email": "a@example.com"}],
            "to": [{"email": "b@example.com"}],
            "textBody": [{"content": "hi"}],
        }
    )
    headers = reply.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
    assert not any(line.lower().startswith(b"bcc:") for line in headers), headers
    assert b"Subject: Re: fooBcc: x@y" in headers

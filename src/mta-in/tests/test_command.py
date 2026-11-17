"""Unit tests for :mod:`pymta.command`, the command-line parser."""

from __future__ import annotations

import pytest

from pymta.address import AddressError, validate_envelope_address
from pymta.command import (
    ArgumentNotAscii,
    CommandSyntaxError,
    parse_parameters,
    split_command,
    split_path,
    strip_keyword,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"NOOP", ("NOOP", None)),
        (b"noop", ("NOOP", None)),
        (b"EHLO mx.example", ("EHLO", "mx.example")),
        (b"EHLO   mx.example  ", ("EHLO", "mx.example")),
        # A space and nothing after it: no argument at all.
        (b"RSET ", ("RSET", None)),
        (b"NOOP  ", ("NOOP", "")),
        (b"MAIL FROM:<a@b.example> SIZE=1", ("MAIL", "FROM:<a@b.example> SIZE=1")),
        ("MAIL FROM:<é@b.example>".encode(), ("MAIL", "FROM:<é@b.example>")),
        # Not UTF-8: kept as a surrogate for the address check to refuse.
        (b"MAIL FROM:<\xe9@b.example>", ("MAIL", "FROM:<\udce9@b.example>")),
    ],
)
def test_split_command(line, expected):
    assert split_command(line, utf8=True) == expected


@pytest.mark.parametrize("line", [b"", b" EHLO x", b"\xc3\xa9HLO x", b"\xff"])
def test_a_line_without_an_ascii_verb_is_refused(line):
    with pytest.raises(CommandSyntaxError):
        split_command(line, utf8=True)


def test_without_smtputf8_an_argument_is_ascii_or_refused():
    assert split_command(b"MAIL FROM:<a@b.example>", utf8=False) == ("MAIL", "FROM:<a@b.example>")
    with pytest.raises(ArgumentNotAscii):
        split_command("MAIL FROM:<a@exämple.com>".encode(), utf8=False)


@pytest.mark.parametrize("verb", ["EHLO", "HELO", "ehlo"])
def test_a_greeting_name_is_never_refused_for_its_bytes(verb):
    """Refused, the session has no greeting and its MAIL is answered 503."""
    line = verb.encode() + " mx.exämple".encode() + b"\xff"
    assert split_command(line, utf8=False) == (verb.upper(), "mx.exämple\udcff")


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("FROM:<a@b.example>", "<a@b.example>"),
        ("from:<a@b.example>", "<a@b.example>"),
        ("FROM: <a@b.example> ", "<a@b.example>"),
        ("FROM:", ""),
        ("TO:<a@b.example>", None),
        ("FROM", None),
        ("<a@b.example>", None),
        (None, None),
    ],
)
def test_strip_keyword(arg, expected):
    assert strip_keyword("FROM:", arg) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<alice@example.com>", ("alice@example.com", "")),
        ("alice@example.com", ("alice@example.com", "")),
        ("<alice@example.com> SIZE=1 BODY=7BIT", ("alice@example.com", " SIZE=1 BODY=7BIT")),
        ("alice@example.com SIZE=1", ("alice@example.com", " SIZE=1")),
        # Tolerated, as before: no space before the parameters.
        ("<alice@example.com>SIZE=1", ("alice@example.com", "SIZE=1")),
        ("<>", ("<>", "")),
        ("<> SIZE=1", ("<>", " SIZE=1")),
        ("< alice@example.com >", ("alice@example.com", "")),
        # Quoted local-parts are handed on as sent.
        ('<"alice"@example.com>', ('"alice"@example.com', "")),
        ('<"al ice"@example.com> SIZE=1', ('"al ice"@example.com', " SIZE=1")),
        ('<"a>b"@example.com>', ('"a>b"@example.com', "")),
        ('<"a\\"b>c"@example.com>', ('"a\\"b>c"@example.com', "")),
        ('"al ice"@example.com SIZE=1', ('"al ice"@example.com', " SIZE=1")),
        ("<a@[192.0.2.1]>", ("a@[192.0.2.1]", "")),
        ("<a@[IPv6:2001:db8::1]>", ("a@[IPv6:2001:db8::1]", "")),
        # RFC 5321 section 4.1.1.3: the route is accepted and ignored.
        ("<@a.example,@b.example:carol@c.example>", ("carol@c.example", "")),
        # Left for the address check to refuse.
        ("<a b@example.com>", ("a b@example.com", "")),
        ("<a@@example.com>", ("a@@example.com", "")),
        ("<@a.example>", ("@a.example", "")),
    ],
)
def test_split_path(text, expected):
    assert split_path(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        " SIZE=1",
        "<alice@example.com",
        "<alice@example.com SIZE=1",
        "<",
        "< >",
        "<@a.example:>",
        '<"alice@example.com>',
        '<a"b@example.com>',
        '"alice@example.com',
        '<"alice\\"@example.com>',
    ],
)
def test_a_path_that_never_closes_is_refused(text):
    assert split_path(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("  ", {}),
        (" SIZE=100", {"SIZE": "100"}),
        (
            " size=100 Body=8bitmime SMTPUTF8",
            {"SIZE": "100", "BODY": "8bitmime", "SMTPUTF8": None},
        ),
        # The value is kept as sent.
        (" ORCPT=rfc822;Bob@Example.com", {"ORCPT": "rfc822;Bob@Example.com"}),
        (" AUTH=<>", {"AUTH": "<>"}),
        (" A=b=c", {"A": "b=c"}),
        ("\tSIZE=1\r", {"SIZE": "1"}),
        # A keyword may hold hyphens after its first character.
        (" mt-priority=3 X-FOO", {"MT-PRIORITY": "3", "X-FOO": None}),
    ],
)
def test_parse_parameters(text, expected):
    assert parse_parameters(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        " SIZE=",
        " =1",
        " =",
        " -FOO=1",
        " SIZE=1 ²=1",
        " é",
        " SIZE=1 SIZE=2",
        " size=x SIZE=2",
        # U+017F LATIN SMALL LETTER LONG S upper-cases to "S": not the keyword SIZE.
        " " + chr(0x17F) + "IZE=1",
    ],
)
def test_a_malformed_parameter_is_refused(text):
    assert parse_parameters(text) is None


# ---------------------------------------------------------------------------
# The parser and the address check together: what a sender is recorded as.
# ---------------------------------------------------------------------------


def _sender(argument: str) -> str:
    path, _ = split_path(argument)
    return validate_envelope_address(
        path,
        allow_empty=True,
        max_local=None,
        max_domain=255,
        allow_percent=True,
        allow_quoted=True,
        allow_literal=True,
    )


@pytest.mark.parametrize(
    ("argument", "address"),
    [
        ("<alice@example.com>", "alice@example.com"),
        ("alice@example.com", "alice@example.com"),
        ("<>", ""),
        # Quotes are dropped where the content does not need them, kept
        # where it does.
        ('<"alice"@example.com>', "alice@example.com"),
        ('<"a..b"@example.com>', '"a..b"@example.com'),
        ("<a..b@example.com>", '"a..b"@example.com'),
        ('<"al ice"@example.com>', '"al ice"@example.com'),
        ('<"a\\"b"@example.com>', '"a\\"b"@example.com'),
        ("<a@[192.0.2.1]>", "a@[192.0.2.1]"),
        ("<a%b@example.com>", "a%b@example.com"),
        ("<Alice@EXAMPLE.com>", "Alice@example.com"),
    ],
)
def test_a_sender_as_recorded(argument, address):
    assert _sender(argument) == address


@pytest.mark.parametrize(
    "argument",
    [
        "<a b@example.com>",
        "<a\tb@example.com>",
        # RFC 5322 comments are not part of an RFC 5321 path.
        "<alice(comment)@example.com>",
        "<alice@example.com(comment)>",
        "<a,b@example.com>",
        "<a;b@example.com>",
        "<a:b@example.com>",
        "<a\\b@example.com>",
        '<a"b"c@example.com>',
        "<a@@example.com>",
        "<@example.com>",
        "<alice@>",
        "<alice>",
    ],
)
def test_a_sender_that_is_not_an_address_is_refused(argument):
    with pytest.raises(AddressError):
        _sender(argument)

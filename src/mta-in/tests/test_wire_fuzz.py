"""Fuzzing the pure byte and text transforms: line endings, log quoting, the
MAIL/RCPT parameter filter and the MDA response reader.

Run with: make fuzz-mta-in-py
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pymta.command import CommandSyntaxError, parse_parameters, split_command, split_path
from pymta.logfmt import LogfmtFormatter, clip, clip_list, quote
from pymta.mda_async import MDAClient, MDAResult
from pymta.wire import stored_line

from tests.fuzzing import fuzz_settings

pytestmark = pytest.mark.fuzz


# ---------------------------------------------------------------------------
# Line endings.
# ---------------------------------------------------------------------------

# Heavy on the bytes that matter; uniform bytes almost never produce a CR next
# to an LF next to a dot.
_BODY = st.lists(
    st.one_of(
        st.sampled_from([b"\r\n", b"\n", b"\r", b".", b"..", b"\r\n.\r\n", b"\n.\n", b"a", b""]),
        st.binary(max_size=12),
    ),
    max_size=40,
).map(b"".join)


def _reference(content: bytes) -> bytes:
    """The same rule, written as a byte-by-byte scan."""
    out, i = bytearray(), 0
    while i < len(content):
        if content[i : i + 2] == b"\r\n":
            out += b"\r\n"
            i += 2
        elif content[i : i + 1] == b"\n":
            out += b"\r\n"
            i += 1
            # The dot a sender that ends its lines in LF stuffed here.
            if content[i : i + 1] == b".":
                i += 1
        elif content[i : i + 1] == b"\r":
            out += b"\r\n"
            i += 1
        else:
            out.append(content[i])
            i += 1
    return bytes(out)


def _wire_lines(body: bytes) -> list[bytes]:
    """``body`` and its final CRLF as the DATA reader cuts it: at every LF."""
    return [line + b"\n" for line in (body + b"\r\n").split(b"\n")[:-1]]


def _stored(body: bytes) -> tuple[bytes, bool]:
    kept = [stored_line(line) for line in _wire_lines(body)]
    return b"".join(line for line, _ in kept), any(rewritten for _, rewritten in kept)


def two_step_reference(body: bytes) -> bytes:
    """The same rules stated in two passes: unstuff at CRLF, then ``_reference``."""
    lines = (body + b"\r\n").split(b"\r\n")[:-1]
    return _reference(
        b"".join((line[1:] if line.startswith(b".") else line) + b"\r\n" for line in lines)
    )


@settings(**fuzz_settings())
@given(body=_BODY)
def test_stored_lines_have_no_bare_line_ending(body):
    stored, rewritten = _stored(body)

    pairs = stored.count(b"\r\n")
    assert stored.count(b"\n") == pairs
    assert stored.count(b"\r") == pairs
    wire = body + b"\r\n"
    clean = wire.count(b"\n") == wire.count(b"\r\n") == wire.count(b"\r")
    assert rewritten is not clean
    assert stored == two_step_reference(body)


@settings(**fuzz_settings())
@given(line=st.binary(max_size=30).filter(lambda b: b"\r" not in b and b"\n" not in b))
def test_a_clean_line_is_not_copied(line):
    wire = line + b"\r\n"
    stored, rewritten = stored_line(wire)
    assert rewritten is False
    if line.startswith(b"."):
        assert stored == wire[1:]
    else:
        assert stored is wire


@settings(**fuzz_settings())
@given(lines=st.lists(st.binary(max_size=20).filter(lambda b: b"\r" not in b and b"\n" not in b)))
def test_a_dot_stuffed_lf_message_reads_as_its_author_wrote_it(lines):
    """What a sender that ends its lines in LF puts on the wire is stored as
    the original lines with CRLF."""
    stuffed = b"\n".join(b"." + line if line.startswith(b".") else line for line in lines)
    assert _stored(stuffed)[0] == b"\r\n".join(lines) + b"\r\n"


# ---------------------------------------------------------------------------
# Log lines.
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|[^ "]*)')
_UNESCAPE = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def _decode(value: str) -> str:
    if not value.startswith('"'):
        return value
    out, i, body = [], 0, value[1:-1]
    while i < len(body):
        if body[i] != "\\":
            out.append(body[i])
            i += 1
        elif body[i + 1] == "u":
            out.append(chr(int(body[i + 2 : i + 6], 16)))
            i += 6
        elif body[i + 1] == "U":
            out.append(chr(int(body[i + 2 : i + 10], 16)))
            i += 10
        else:
            out.append(_UNESCAPE[body[i + 1]])
            i += 2
    return "".join(out)


# Anything a peer can put in an address, a HELO or a command argument,
# surrogates from undecodable bytes included.
_PEER_TEXT = st.one_of(
    st.text(max_size=60),
    st.text(alphabet=st.sampled_from(list('a =\\"\n\r\t\x00\x1b\x85\u2028\u2029')), max_size=30),
    st.binary(max_size=40).map(lambda raw: raw.decode("utf-8", "surrogateescape")),
)


@settings(**fuzz_settings())
@given(sender=_PEER_TEXT, helo=_PEER_TEXT, arg=_PEER_TEXT)
def test_a_log_line_carries_exactly_the_fields_it_was_given(sender, helo, arg):
    """No value can end the line, start a second record or add a field."""
    record = logging.LogRecord("pymta.test", logging.INFO, __file__, 1, "reject", (), None)
    record.__dict__.update({"sender": sender, "helo": helo, "arg": arg, "code": 550})
    line = LogfmtFormatter().format(record)

    assert len(line.splitlines()) == 1
    assert all(char.isprintable() for char in line)
    line.encode("utf-8")

    # The whole line is tokens separated by single spaces, nothing left over.
    tokens = _TOKEN.findall(line)
    assert " ".join(f"{key}={value}" for key, value in tokens) == line
    fields = {key: _decode(value) for key, value in tokens}
    assert list(fields) == ["ts", "level", "logger", "event", "sender", "helo", "arg", "code"]
    assert (fields["sender"], fields["helo"], fields["arg"]) == (sender, helo, arg)
    assert (fields["event"], fields["level"], fields["code"]) == ("reject", "info", "550")


@settings(**fuzz_settings())
@given(value=_PEER_TEXT)
def test_a_quoted_value_reads_back_as_itself(value):
    token = quote(value)
    assert _TOKEN.fullmatch(f"k={token}"), token
    assert _decode(token) == value


@settings(**fuzz_settings())
@given(
    value=_PEER_TEXT,
    limit=st.integers(min_value=1, max_value=80),
    tail=st.integers(min_value=0, max_value=80),
)
def test_clip_bounds_what_it_returns(value, limit, tail):
    tail = min(tail, limit)
    clipped = clip(value, limit, tail=tail)
    if len(value) <= limit:
        assert clipped == value
    else:
        # The kept characters plus a marker that says how many went.
        assert len(clipped) <= limit + len(f"...+{len(value)}...")
        assert f"+{len(value) - limit}" in clipped


@settings(**fuzz_settings())
@given(
    items=st.lists(st.text(max_size=10), max_size=30), limit=st.integers(min_value=1, max_value=12)
)
def test_clip_list_names_at_most_the_limit(items, limit):
    clipped = clip_list(items, limit)
    if len(items) <= limit:
        assert clipped == ",".join(items)
    else:
        assert clipped.endswith(f",+{len(items) - limit} more")


# ---------------------------------------------------------------------------
# MAIL/RCPT parameters accepted and ignored.
# ---------------------------------------------------------------------------

_PARAM = st.one_of(
    st.sampled_from(
        [
            "RET=HDRS",
            "ret=full",
            "ENVID=QQ1",
            "AUTH=<>",
            "AUTH=a@b.example",
            "NOTIFY=NEVER",
            "ORCPT=rfc822;a@b.example",
            "SIZE=100",
            "BODY=8BITMIME",
            "SMTPUTF8",
            "REQUIRETLS",
            "XFOO=BAR",
            "RETX=1",
            "=",
            "RET=",
        ]
    ),
    st.text(alphabet=st.characters(blacklist_characters=" \r\n"), min_size=1, max_size=12),
)
_PATH = st.one_of(
    st.sampled_from(["<a@b.example>", "a@b.example", "<>", '<"a b"@c.example>', "<a@b.example"]),
    st.text(max_size=30),
)


@settings(**fuzz_settings())
@given(path=_PATH, params=st.lists(_PARAM, max_size=6), gap=st.sampled_from([" ", "  ", ""]))
def test_parameters_never_change_the_path_that_is_read(path, params, gap):
    """What follows a complete path cannot reach back into it: the address
    read is the same whatever parameters come after."""
    alone = split_path(path)
    followed = split_path(path + gap + " ".join(params))
    if alone is None:
        # Not a path on its own: there is nothing to compare.
        return
    if not gap and params and not path.startswith("<"):
        # Glued to an unbracketed path, the text is part of it.
        return
    assert followed is not None
    assert followed[0] == alone[0]
    assert followed[1].strip() == (alone[1] + gap + " ".join(params)).strip()


@settings(**fuzz_settings())
@given(text=st.text(max_size=80))
def test_the_command_parser_is_total(text):
    """Any text: a result of the documented shape, never an exception."""
    split = split_path(text)
    if split is not None:
        path, rest = split
        assert path and text.endswith(rest)
        if text.startswith("<"):
            # What was inside the brackets, without the spaces at its edges.
            assert path == "<>" or path == path.strip(" \t")
        else:
            # Up to the first space, as sent.
            assert text.startswith(path) and " " not in path.partition('"')[0]
    parameters = parse_parameters(text)
    if parameters is not None:
        assert all(
            name.isascii() and name.isalnum() and name == name.upper() for name in parameters
        )
        assert all(value is None or value for value in parameters.values())
    for utf8 in (True, False):
        try:
            verb, arg = split_command(text.encode("utf-8"), utf8=utf8)
        except CommandSyntaxError:
            continue
        assert verb.isascii() and verb == verb.upper() and " " not in verb
        assert arg is None or arg == arg.strip(" \t\n\r\x0b\x0c")


# ---------------------------------------------------------------------------
# What the MDA answers.
# ---------------------------------------------------------------------------


class _OneResponse:
    def __init__(self, response):
        self.response = response

    async def post(self, url, content=None, headers=None):
        if hasattr(content, "__aiter__"):
            async for _ in content:
                pass
        return self.response

    async def aclose(self):
        pass


_JSON_BODY = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=10),
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=6), children, max_size=3)
    ),
    max_leaves=6,
).map(lambda value: httpx.Response(200, json=value).content)


@settings(**fuzz_settings())
@given(
    status=st.integers(min_value=100, max_value=599),
    body=st.one_of(st.binary(max_size=60), _JSON_BODY),
    deliver=st.booleans(),
)
def test_any_mda_response_becomes_a_result(status, body, deliver):
    """A proxy page, a truncated body, a JSON list: never an exception, and
    only 400 and 413 on a delivery may bounce the message."""

    async def run():
        client = MDAClient(base_url="https://mda.invalid/api/", secret="x" * 32, timeout=5)
        client._client = _OneResponse(httpx.Response(status, content=body))
        if not deliver:
            return await client.check_recipient("a@example.com")
        return await client.deliver(
            message=b"Subject: x\r\n\r\nbody\r\n",
            sender="a@example.com",
            original_recipients=["b@example.com"],
            client_address="192.0.2.1",
            client_port="25",
            client_helo="mx.example",
        )

    result = asyncio.run(run())

    assert isinstance(result, MDAResult)
    assert isinstance(result.payload, dict)
    assert result.ok == (status == 200)
    if not result.ok:
        # And only with the MDA's own body.
        verdict = result.payload.get("status") == "error"
        permanent = deliver and status in (400, 413) and verdict
        assert result.temp_fail == (not permanent)

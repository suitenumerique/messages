"""Fuzzing whole SMTP sessions against an in-process server.

Each example is one connection: a sequence of command lines, some well-formed,
some not, some raw bytes, against the real ``SMTPConnection`` + ``InboundHandler``
and an MDA whose answers are part of the example. What is checked is what
must hold whatever a peer sends:

* no exception of ours (``handler_error``, ``session_error``, ``unhandled_error``);
* one reply per line, until the server hangs up;
* every 4xx/5xx has its log line, and the session has its summary;
* the session slot is given back;
* what reaches the MDA is clean: validated addresses, CRLF-only content, a
  HELO that can be stored;
* a message is acknowledged with 250 only if the MDA took it.

Run with: make fuzz-mta-in-py
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pymta import metrics, runtime
from pymta import settings as pymta_settings
from pymta.address import AddressError, validate_envelope_address
from pymta.controller import build_smtp_kwargs
from pymta.handler import InboundHandler
from pymta.limits import IPGate
from pymta.mda_async import MDAResult
from pymta.smtp_protocol import SMTPConnection

from tests.fuzzing import fuzz_settings
from tests.harness import LOOPBACK_PROXIES
from tests.test_wire_fuzz import two_step_reference

pytestmark = pytest.mark.fuzz

_OUR_FAULTS = {"handler_error", "session_error", "unhandled_error"}
# The INFO (or WARNING) line a refused command leaves.
_REFUSAL_EVENTS = {"reject", "message_deferred", "message_rejected", "mda_check_no_verdict"}

_ADDRESS = st.one_of(
    st.sampled_from(
        [
            "<alice@sender.example>",
            "<bob@example.com>",
            "<>",
            "alice@sender.example",
            "<bounce-a%40b.example@esp.example>",
            "<root@localhost>",
            "<postmaster>",
            '<"a b"@c.example>',
            "<a@[192.0.2.1]>",
            "<.a@b.example>",
            "<@a.example:b@c.example>",
            "<a@b.example",
            "<" + "a" * 70 + "@example.com>",
        ]
    ),
    st.text(
        alphabet=st.characters(blacklist_characters="\n", blacklist_categories=("Cs",)),
        max_size=30,
    ).map(lambda t: f"<{t}@example.com>"),
    # Raw bytes, not UTF-8 for the most part.
    st.binary(max_size=20)
    .filter(lambda b: b"\n" not in b)
    .map(lambda b: b.decode("utf-8", "surrogateescape")),
)
_PARAMS = st.lists(
    st.sampled_from(
        [
            "SIZE=100",
            "SIZE=99999999999",
            "SIZE=x",
            "BODY=8BITMIME",
            "BODY=BINARYMIME",
            "SMTPUTF8",
            "RET=HDRS",
            "ENVID=Q1",
            "AUTH=<>",
            "NOTIFY=NEVER",
            "ORCPT=rfc822;a@b.example",
            "REQUIRETLS",
            "XFOO=BAR",
            "=",
        ]
    ),
    max_size=3,
).map(lambda params: "".join(" " + p for p in params))

_COMMAND = st.one_of(
    st.sampled_from(
        [
            "EHLO mx.sender.example",
            "HELO mx.sender.example",
            "EHLO",
            "EHLO mx\t.sender.example",
            "RSET",
            "NOOP",
            "DATA",
            "DATA now",
            "VRFY bob",
            "EXPN staff",
            "HELP",
            "STARTTLS",
            "AUTH PLAIN AGFsaWNlAHNlY3JldA==",
            "MAIL",
            "RCPT",
            "QUIT",
            "BDAT 10 LAST",
            "FROBNICATE",
        ]
    ),
    st.builds(lambda address, params: f"MAIL FROM:{address}{params}", _ADDRESS, _PARAMS),
    st.builds(lambda address, params: f"RCPT TO:{address}{params}", _ADDRESS, _PARAMS),
    st.builds(lambda helo: f"EHLO {helo}", _ADDRESS),
).map(lambda text: text.encode("utf-8", "surrogateescape"))

# Lines that are not commands at all. No LF: one line sent is one line read.
_NOISE = st.binary(max_size=700).filter(lambda b: b"\n" not in b)

# Random lines alone almost never add up to a message: a whole transaction is
# generated as such, mostly valid, so that the delivery path is exercised with
# odd greetings, odd parameters and the occasional bad address in the middle.
_GOOD_SENDER = st.sampled_from(
    ["<alice@sender.example>", "<>", "<bounce-a%40b.example@esp.example>", "alice@sender.example"]
)
_GOOD_RECIPIENT = st.sampled_from(
    ["<bob@example.com>", "<carol+tag@example.com>", "bob@example.com", "<Dave@Example.COM>"]
)
_GOOD_PARAMS = st.lists(
    st.sampled_from(["SIZE=100", "BODY=8BITMIME", "SMTPUTF8", "RET=HDRS", "ENVID=Q1", "AUTH=<>"]),
    max_size=2,
    unique=True,
).map(lambda params: "".join(" " + p for p in params))
_GREETING = st.one_of(
    st.just("mx.sender.example"),
    st.text(
        alphabet=st.characters(blacklist_characters="\n", blacklist_categories=("Cs",)),
        min_size=1,
        max_size=20,
    ),
    st.binary(min_size=1, max_size=20)
    .filter(lambda b: b"\n" not in b)
    .map(lambda b: b.decode("utf-8", "surrogateescape")),
)
_TRANSACTION = st.builds(
    lambda verb, greeting, sender, params, recipients: [
        f"{verb} {greeting}",
        f"MAIL FROM:{sender}{params}",
        *[f"RCPT TO:{recipient}" for recipient in recipients],
        "DATA",
    ],
    st.sampled_from(["EHLO", "EHLO", "HELO"]),
    _GREETING,
    st.one_of(_GOOD_SENDER, _GOOD_SENDER, _ADDRESS),
    _GOOD_PARAMS,
    st.lists(st.one_of(_GOOD_RECIPIENT, _GOOD_RECIPIENT, _ADDRESS), min_size=1, max_size=4),
).map(lambda lines: [line.encode("utf-8", "surrogateescape") for line in lines])

_SESSION = st.one_of(
    st.lists(
        st.one_of(_TRANSACTION, _TRANSACTION, st.lists(st.one_of(_COMMAND, _NOISE), max_size=6)),
        max_size=4,
    ).map(lambda parts: [line for part in parts for line in part]),
    # No structure at all: any command or noise, in any order.
    st.lists(st.one_of(_COMMAND, _COMMAND, _COMMAND, _NOISE), max_size=30),
)

_BODY = st.lists(
    st.one_of(
        st.sampled_from(
            [b"Subject: x\r\n", b"\r\n", b"body\r\n", b"\n", b"\r", b"..\r\n", b"\x00"]
        ),
        st.binary(max_size=30),
    ),
    max_size=8,
).map(b"".join)
# The client ends DATA itself: a body that holds the terminator, alone or with
# the CRLF around it, would end it early and turn its tail into commands.
_BODY = _BODY.filter(lambda body: b"\r\n.\r\n" not in b"\r\n" + body + b"\r\n")

# What the MDA answers to a recipient check, and to a delivery.
#
# "yes" is repeated so most recipients are accepted and a delivery is reached.
# Every delivery is scripted (the list is never short): left to a default,
# the unusual answers were drawn too rarely to be tested.
_CHECK = st.sampled_from(["yes", "yes", "yes", "no", "temp", "no_verdict", "garbage"])
_DELIVER = st.sampled_from(["odd_200", "partial", "temp", "perm", "ok", "ok"])
_DELIVERIES = st.lists(_DELIVER, min_size=6, max_size=6)


class _ScriptedMDA:
    def __init__(self, checks, delivers):
        self.checks, self.delivers = list(checks), list(delivers)
        self.checked: list[str] = []
        self.delivered: list[dict] = []
        self.accepted = 0

    async def check_recipient(self, address, session=None):
        self.checked.append(address)
        answer = self.checks.pop(0) if self.checks else "yes"
        if answer == "temp":
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=503)
        payload = {
            "yes": {address: True},
            "no": {address: False},
            "no_verdict": {},
            "garbage": {address: "true"},
        }[answer]
        return MDAResult(ok=True, temp_fail=False, payload=payload, status_code=200)

    async def deliver(self, **kwargs):
        self.delivered.append(kwargs)
        answer = self.delivers.pop(0) if self.delivers else "ok"
        if answer == "ok":
            self.accepted += 1
            return MDAResult(ok=True, temp_fail=False, payload={"status": "ok"}, status_code=200)
        if answer == "odd_200":
            return MDAResult(ok=True, temp_fail=False, payload={}, status_code=200)
        if answer == "perm":
            return MDAResult(ok=False, temp_fail=False, payload={}, status_code=400)
        status = 207 if answer == "partial" else 503
        return MDAResult(ok=False, temp_fail=True, payload={}, status_code=status)


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


async def _read_reply(reader) -> bytes | None:
    """The last line of one reply, or None when the server has hung up."""
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not line:
            return None
        assert line[:3].isdigit() and line[3:4] in (b" ", b"-") and line.endswith(b"\r\n"), line
        if line[3:4] == b" ":
            return line


# Where each write is cut into TCP segments. Often nowhere.
_CUTS = st.lists(st.integers(min_value=0, max_value=4000), max_size=4)


async def _write(writer, data: bytes, cuts) -> None:
    """Send ``data`` split at ``cuts``, one TCP write per piece.

    A command or a body arrives in whatever pieces the network makes of it: a
    CRLF, a terminating dot or a multi-byte character can be cut anywhere.
    """
    points = sorted({cut % len(data) for cut in cuts}) if data else []
    start = 0
    for point in [*points, len(data)]:
        if point > start:
            writer.write(data[start:point])
            try:
                await writer.drain()
            except ConnectionError:
                # Already hung up on: the read that follows will say so.
                return
            # Let the server read this piece before the next one is sent.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            start = point


class _RefusedAtTheCap(Exception):
    """The greeting was the 421 of a full server."""


async def _session(lines, bodies, mda, cuts=(), gate=None):
    """Run one connection. Returns (replies, data_replies, hung_up)."""
    loop = asyncio.get_running_loop()
    gate = gate or IPGate(max_total=None)
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(InboundHandler(mda), ip_gate=gate, **kwargs),
        host="127.0.0.1",
        port=0,
    )
    port = server.sockets[0].getsockname()[1]
    replies, data_replies, hung_up = [], [], False
    bodies = list(bodies)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            greeting = await _read_reply(reader)
            if greeting is not None and greeting.startswith(b"421 4.7.0 Too many connections"):
                raise _RefusedAtTheCap
            assert greeting is not None and greeting.startswith(b"220"), greeting
            for line in lines:
                await _write(writer, line + b"\r\n", cuts)
                reply = await _read_reply(reader)
                if reply is None:
                    hung_up = True
                    break
                replies.append(reply)
                if reply.startswith(b"354"):
                    await _write(
                        writer, (bodies.pop(0) if bodies else b"x\r\n") + b"\r\n.\r\n", cuts
                    )
                    reply = await _read_reply(reader)
                    if reply is None:
                        hung_up = True
                        break
                    replies.append(reply)
                    data_replies.append(reply)
        except ConnectionError:
            # Writing to a connection the server has closed.
            hung_up = True
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        # connection_lost and the gate release run a few iterations later.
        for _ in range(200):
            if metrics.active_sessions() == 0:
                break
            await asyncio.sleep(0.005)
    finally:
        server.close()
        await server.wait_closed()
    return replies, data_replies, hung_up


@settings(**fuzz_settings(divisor=4))
@given(
    lines=_SESSION,
    bodies=st.lists(_BODY, max_size=4),
    checks=st.lists(_CHECK, max_size=10),
    delivers=_DELIVERIES,
    cuts=_CUTS,
)
def test_no_session_breaks_the_server_or_escapes_the_log(lines, bodies, checks, delivers, cuts):
    mda = _ScriptedMDA(checks, delivers)
    records = _Records()
    logger = logging.getLogger("pymta")
    previous_level = logger.level
    logger.addHandler(records)
    logger.setLevel(logging.DEBUG)
    try:
        replies, data_replies, hung_up = asyncio.run(_session(lines, bodies, mda, cuts))
    finally:
        logger.removeHandler(records)
        logger.setLevel(previous_level)
        runtime.reset_for_tests()

    events = [record.getMessage() for record in records.records]

    # Nothing of ours raised.
    assert not _OUR_FAULTS & set(events), [
        (r.getMessage(), r.exc_info) for r in records.records if r.getMessage() in _OUR_FAULTS
    ]

    # One reply per line until the server closed, and it only closes with a
    # reason: a 221, a 421, or the 502 "goodbye" for unrecognised commands.
    if hung_up:
        assert replies and (replies[-1][:3] in (b"221", b"421") or b"goodbye" in replies[-1]), (
            replies
        )
    else:
        assert len(replies) == len(lines) + len(data_replies)

    # The slot is back.
    assert metrics.active_sessions() == 0

    # Every refusal has its line, and the session its summary.
    refused = [reply for reply in replies if reply[:1] in (b"4", b"5")]
    summaries = [r for r in records.records if r.getMessage() == "session_closed"]
    assert len(summaries) == 1
    assert summaries[0].errors == len(refused)
    logged = [r for r in records.records if r.getMessage() in _REFUSAL_EVENTS]
    assert len(logged) == len(refused), (events, replies)
    for record in logged:
        assert record.session_id == summaries[0].session_id
        assert record.client_ip == "127.0.0.1"
    # The budget closes the session; DATA and QUIT are the only verbs past it.
    assert len(refused) <= pymta_settings.PYMTA_MAX_ERRORS_PER_SESSION + 14

    # What the MDA was asked and given is clean.
    for address in mda.checked:
        assert _is_clean_recipient(address), address
    for delivery in mda.delivered:
        assert delivery["original_recipients"], delivery
        for address in delivery["original_recipients"]:
            assert _is_clean_recipient(address), address
        if delivery["sender"]:
            assert _is_clean_sender(delivery["sender"]), delivery["sender"]
        message = delivery["message"]
        pairs = message.count(b"\r\n")
        assert message.count(b"\n") == pairs and message.count(b"\r") == pairs
        assert b"\x00" not in message
        assert len(message) <= pymta_settings.PYMTA_MAX_INCOMING_EMAIL_SIZE
        helo = delivery["client_helo"]
        assert helo and not set(helo) & set("\r\n\x00\t")
        helo.encode("utf-8")
        assert delivery["client_address"] == "127.0.0.1"

    # A 250 at the end of DATA means the MDA took the message, and only then.
    assert sum(reply.startswith(b"250") for reply in data_replies) == mda.accepted


def _is_clean_recipient(address: str) -> bool:
    try:
        return address == validate_envelope_address(
            address,
            allow_empty=False,
            max_local=pymta_settings.PYMTA_MAX_LOCAL_PART,
            max_domain=pymta_settings.PYMTA_MAX_DOMAIN,
        )
    except AddressError:
        return False


def _is_clean_sender(address: str) -> bool:
    try:
        return address == validate_envelope_address(
            address,
            allow_empty=True,
            max_local=None,
            max_domain=pymta_settings.PYMTA_MAX_DOMAIN,
            allow_percent=True,
            require_fqdn=pymta_settings.PYMTA_REQUIRE_FQDN_SENDER,
            allow_quoted=True,
            allow_literal=not pymta_settings.PYMTA_REQUIRE_FQDN_SENDER,
        )
    except AddressError:
        return False


# ---------------------------------------------------------------------------
# What is stored is what was sent: a differential test of the DATA path.
#
# The expected bytes come from a second, independent statement of the rules
# (split on CRLF, one stuffing dot per line, then the line-ending rule as
# test_wire_fuzz writes it), not from the code under test.
# ---------------------------------------------------------------------------

_SMALL_LIMIT = 1500

_RAW_BODY = st.lists(
    st.one_of(
        st.sampled_from(
            [b"\r\n", b"\n", b"\r", b".", b"..", b"...", b"\n.", b"\r.", b" ", b"\x00", b"a"]
        ),
        st.binary(max_size=40),
        st.binary(min_size=200, max_size=600),
    ),
    max_size=14,
).map(b"".join)
_RAW_BODY = _RAW_BODY.filter(lambda body: b"\r\n.\r\n" not in b"\r\n" + body + b"\r\n")


def _expected(body: bytes) -> tuple[bytes, bytes | None]:
    """(reply code, stored bytes) for ``body`` followed by the terminator."""
    stored = two_step_reference(body)
    # The size limit is on what is stored, and applies before the handler
    # sees the message.
    if len(stored) > _SMALL_LIMIT:
        return b"552", None
    if b"\x00" in stored:
        return b"554", None
    return b"250", stored


@settings(**fuzz_settings(divisor=2))
@given(body=_RAW_BODY, cuts=_CUTS)
def test_the_message_stored_is_the_message_sent(body, cuts):
    mda = _ScriptedMDA([], ["ok"] * 6)
    lines = [
        b"EHLO mx.sender.example",
        b"MAIL FROM:<alice@sender.example>",
        b"RCPT TO:<bob@example.com>",
        b"DATA",
        b"NOOP",
    ]
    previous = pymta_settings.PYMTA_MAX_INCOMING_EMAIL_SIZE
    pymta_settings.PYMTA_MAX_INCOMING_EMAIL_SIZE = _SMALL_LIMIT
    try:
        replies, data_replies, hung_up = asyncio.run(_session(lines, [body], mda, cuts))
    finally:
        pymta_settings.PYMTA_MAX_INCOMING_EMAIL_SIZE = previous
        runtime.reset_for_tests()

    code, stored = _expected(body)
    assert not hung_up, replies
    assert [reply[:3] for reply in data_replies] == [code], (replies, body)
    # Whatever became of the message, the session is where it should be: the
    # body is over and the next line is read as a command.
    assert replies[-1].startswith(b"250"), replies
    assert [delivery["message"] for delivery in mda.delivered] == ([stored] if stored else [])


# ---------------------------------------------------------------------------
# A peer that does not wait for replies: the whole session in one write.
#
# The properties above read each reply before sending the next line, which is
# what a correct client does and what hides everything about pipelining.
# ---------------------------------------------------------------------------


async def _burst(lines, mda, *, before_greeting):
    loop = asyncio.get_running_loop()
    gate = IPGate(max_total=None)
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(InboundHandler(mda), ip_gate=gate, **kwargs),
        host="127.0.0.1",
        port=0,
    )
    port = server.sockets[0].getsockname()[1]
    answer = b""
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            if not before_greeting:
                answer += await asyncio.wait_for(reader.readline(), timeout=10)
            writer.write(b"".join(line + b"\r\n" for line in lines))
            await writer.drain()
            writer.write_eof()
            answer += await asyncio.wait_for(reader.read(), timeout=10)
        except ConnectionError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        for _ in range(200):
            if metrics.active_sessions() == 0:
                break
            await asyncio.sleep(0.005)
    finally:
        server.close()
        await server.wait_closed()
    return answer, gate


_SYNC = b"554 5.5.0 Error: SMTP protocol synchronization"


@settings(**fuzz_settings(divisor=4))
@given(
    lines=_SESSION.filter(lambda lines: len(lines) >= 2 and sum(map(len, lines)) < 8000),
    checks=st.lists(_CHECK, max_size=10),
    before_greeting=st.booleans(),
)
def test_a_session_sent_in_one_burst_delivers_nothing(lines, checks, before_greeting):
    mda = _ScriptedMDA(checks, ["ok"] * 6)
    records = _Records()
    logger = logging.getLogger("pymta")
    previous_level = logger.level
    logger.addHandler(records)
    logger.setLevel(logging.DEBUG)
    try:
        answer, gate = asyncio.run(_burst(lines, mda, before_greeting=before_greeting))
    finally:
        logger.removeHandler(records)
        logger.setLevel(previous_level)
        runtime.reset_for_tests()

    faults = [r for r in records.records if r.getMessage() in _OUR_FAULTS]
    assert not faults, [(r.getMessage(), r.exc_info) for r in faults]
    assert gate._total == 0 and metrics.active_sessions() == 0
    replies = answer.split(b"\r\n")[:-1]
    for reply in replies:
        assert reply[:3].isdigit() and reply[3:4] in (b" ", b"-"), answer

    # No message can come out of this: a body is only ever read after a 354,
    # and a DATA with input already waiting behind it never gets one.
    assert mda.delivered == []
    assert not any(reply.startswith(b"354") for reply in replies[:-1]), answer

    if before_greeting:
        # Not even a greeting.
        assert replies == [_SYNC], answer
    elif lines[0].split(b" ")[0].upper() in (b"EHLO", b"HELO"):
        # The usual first command, with the rest of the session behind it.
        assert replies[1:] == [_SYNC], answer


# ---------------------------------------------------------------------------
# Several sessions at once, on a server with few slots.
# ---------------------------------------------------------------------------


@settings(**fuzz_settings(divisor=8))
@given(
    sessions=st.lists(
        st.tuples(_SESSION, st.lists(_BODY, max_size=2), _CUTS), min_size=2, max_size=5
    ),
    checks=st.lists(_CHECK, max_size=10),
    delivers=_DELIVERIES,
    slots=st.integers(min_value=1, max_value=4),
)
def test_concurrent_sessions_leave_the_gate_as_they_found_it(sessions, checks, delivers, slots):
    """Refused at the cap, cut mid-command or served: every slot comes back."""
    mda = _ScriptedMDA(checks, delivers * 4)
    records = _Records()
    logger = logging.getLogger("pymta")
    previous_level = logger.level
    logger.addHandler(records)
    logger.setLevel(logging.DEBUG)
    gate = IPGate(max_total=slots, max_data=1)

    async def one(lines, bodies, cuts):
        try:
            return await _session(lines, bodies, mda, cuts, gate)
        except _RefusedAtTheCap:
            return None

    async def all_of_them():
        return await asyncio.gather(*(one(*session) for session in sessions))

    try:
        asyncio.run(all_of_them())
    finally:
        logger.removeHandler(records)
        logger.setLevel(previous_level)
        runtime.reset_for_tests()

    faults = [r for r in records.records if r.getMessage() in _OUR_FAULTS]
    assert not faults, [(r.getMessage(), r.exc_info) for r in faults]
    assert gate._total == 0
    assert gate._data_total == 0
    assert not gate._data_per_ip
    assert metrics.active_sessions() == 0
    for delivery in mda.delivered:
        assert delivery["original_recipients"]
        message = delivery["message"]
        assert message.count(b"\n") == message.count(b"\r\n") == message.count(b"\r")


# ---------------------------------------------------------------------------
# PROXY protocol headers.
# ---------------------------------------------------------------------------

_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"
_PROXY_HEADER = st.one_of(
    st.sampled_from(
        [
            b"PROXY TCP4 203.0.113.9 192.0.2.1 4321 25\r\n",
            b"PROXY TCP6 2001:db8::9 2001:db8::1 4321 25\r\n",
            b"PROXY UNKNOWN\r\n",
            _V2_SIGNATURE + b"\x20\x00\x00\x00",
            _V2_SIGNATURE
            + b"\x21\x11\x00\x0c"
            + bytes([203, 0, 113, 9, 192, 0, 2, 1, 16, 225, 0, 25]),
        ]
    ),
    st.builds(
        lambda family, src, dst, sport, dport, end: (
            f"PROXY {family} {src} {dst} {sport} {dport}".encode() + end
        ),
        st.sampled_from(["TCP4", "TCP6", "UNKNOWN", "TCP5", ""]),
        st.one_of(st.ip_addresses().map(str), st.text(max_size=12)),
        st.one_of(st.ip_addresses().map(str), st.text(max_size=12)),
        st.one_of(st.integers(-1, 70000).map(str), st.text(max_size=5)),
        st.one_of(st.integers(-1, 70000).map(str), st.text(max_size=5)),
        st.sampled_from([b"\r\n", b"\n", b"", b"\r\n\r\n"]),
    ),
    st.builds(lambda tail: _V2_SIGNATURE + tail, st.binary(max_size=60)),
    st.binary(max_size=150),
)


async def _proxied_session(header, cuts, mda):
    loop = asyncio.get_running_loop()
    gate = IPGate(max_total=0)
    kwargs = build_smtp_kwargs(tls_context=None)
    server = await loop.create_server(
        lambda: SMTPConnection(InboundHandler(mda), ip_gate=gate, **kwargs),
        host="127.0.0.1",
        port=0,
    )
    port = server.sockets[0].getsockname()[1]
    greeted = False
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _write(writer, header, cuts)
            first = await asyncio.wait_for(reader.readline(), timeout=10)
            greeted = first.startswith(b"220")
            if greeted:
                for line in (
                    b"EHLO mx.sender.example",
                    b"MAIL FROM:<alice@sender.example>",
                    b"RCPT TO:<bob@example.com>",
                    b"DATA",
                ):
                    writer.write(line + b"\r\n")
                    await writer.drain()
                    if await _read_reply(reader) is None:
                        break
                else:
                    writer.write(b"Subject: x\r\n\r\nb\r\n.\r\n")
                    await writer.drain()
                    await _read_reply(reader)
        except ConnectionError, TimeoutError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        for _ in range(200):
            if metrics.active_sessions() == 0:
                break
            await asyncio.sleep(0.005)
    finally:
        server.close()
        await server.wait_closed()
    return greeted, gate


@settings(**fuzz_settings(divisor=8))
@given(header=_PROXY_HEADER, cuts=_CUTS)
def test_any_proxy_header_is_accepted_cleanly_or_refused_cleanly(header, cuts):
    mda = _ScriptedMDA([], ["ok"] * 6)
    records = _Records()
    logger = logging.getLogger("pymta")
    previous_level = logger.level
    logger.addHandler(records)
    logger.setLevel(logging.DEBUG)
    saved = (
        pymta_settings.PYMTA_ENABLE_PROXY_PROTOCOL,
        pymta_settings.PYMTA_PROXY_PROTOCOL_TIMEOUT,
        pymta_settings.PYMTA_TRUSTED_PROXIES,
    )
    pymta_settings.PYMTA_ENABLE_PROXY_PROTOCOL = True
    pymta_settings.PYMTA_PROXY_PROTOCOL_TIMEOUT = 0.3
    pymta_settings.PYMTA_TRUSTED_PROXIES = LOOPBACK_PROXIES
    try:
        greeted, gate = asyncio.run(_proxied_session(header, cuts, mda))
    finally:
        (
            pymta_settings.PYMTA_ENABLE_PROXY_PROTOCOL,
            pymta_settings.PYMTA_PROXY_PROTOCOL_TIMEOUT,
            pymta_settings.PYMTA_TRUSTED_PROXIES,
        ) = saved
        logger.removeHandler(records)
        logger.setLevel(previous_level)
        runtime.reset_for_tests()

    faults = [r for r in records.records if r.getMessage() in _OUR_FAULTS]
    assert not faults, [(r.getMessage(), r.exc_info) for r in faults]
    assert gate._total == 0 and metrics.active_sessions() == 0
    if not greeted:
        assert mda.delivered == []
    for delivery in mda.delivered:
        # The client is the address the header named, or nobody: never the
        # balancer, and never text that is not an address.
        address = delivery["client_address"]
        assert address is None or pymta_settings.parse_client_ip(address) is not None

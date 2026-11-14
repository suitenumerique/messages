"""Unit tests for :mod:`pymta.wire`, the receive buffer."""

from __future__ import annotations

import asyncio

import pytest

from pymta.wire import ConnectionClosed, LineReader, LineTooLong


class _Transport:
    def __init__(self):
        self.paused = False
        self.calls: list[str] = []

    def pause_reading(self):
        self.paused = True
        self.calls.append("pause")

    def resume_reading(self):
        self.paused = False
        self.calls.append("resume")


def _reader(limit: int = 10) -> tuple[LineReader, _Transport]:
    transport = _Transport()
    return LineReader(transport, limit), transport


async def _soon(coroutine):
    """Start ``coroutine`` and let it run up to its first wait."""
    task = asyncio.ensure_future(coroutine)
    await asyncio.sleep(0)
    return task


@pytest.mark.asyncio
async def test_lines_come_out_one_at_a_time_with_their_terminator():
    reader, _ = _reader()
    reader.feed(b"one\r\ntwo\nthree")
    assert reader.pending()
    assert await reader.readline() == b"one\r\n"
    assert await reader.readline() == b"two\n"
    waiting = await _soon(reader.readline())
    assert not waiting.done()
    reader.feed(b"\n")
    assert await waiting == b"three\n"
    assert not reader.pending()


@pytest.mark.asyncio
async def test_a_terminator_cut_in_two_by_the_network_is_still_found():
    reader, _ = _reader()
    reader.feed(b"abc\r")
    waiting = await _soon(reader.readline(b"\r\n"))
    assert not waiting.done()
    reader.feed(b"\n")
    assert await waiting == b"abc\r\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [10, 11])
async def test_the_limit_is_on_the_line_without_its_terminator(length):
    reader, _ = _reader(limit=10)
    reader.feed(b"a" * length + b"\n")
    if length == 10:
        assert await reader.readline() == b"a" * 10 + b"\n"
    else:
        with pytest.raises(LineTooLong):
            await reader.readline()
        # Nothing was consumed: the caller decides what becomes of the line.
        assert reader.pending()


@pytest.mark.asyncio
async def test_a_line_with_no_end_is_too_long_as_soon_as_it_passes_the_limit():
    reader, _ = _reader(limit=10)
    reader.feed(b"a" * 11)
    with pytest.raises(LineTooLong):
        await reader.readline()
    # A limit given for one call takes the place of the reader's.
    with pytest.raises(LineTooLong):
        await reader.readline(limit=3)
    reader.feed(b"\n")
    assert await reader.readline(limit=11) == b"a" * 11 + b"\n"


@pytest.mark.asyncio
async def test_skip_line_discards_through_the_next_terminator_only():
    reader, _ = _reader()
    reader.feed(b"a" * 50 + b"\r\nnext\r\n")
    # Returns what it discarded, terminator included.
    assert await reader.skip_line(b"\r\n") == 52
    assert await reader.readline(b"\r\n") == b"next\r\n"


@pytest.mark.asyncio
async def test_skip_line_keeps_nothing_of_a_line_that_arrives_in_pieces():
    reader, _ = _reader()
    reader.feed(b"a" * 30 + b"\r")
    skipping = await _soon(reader.skip_line(b"\r\n"))
    # All of it is dropped but the byte that may start the terminator.
    assert not skipping.done()
    reader.feed(b"\nnext\r\n")
    assert await skipping == 32
    assert await reader.readline(b"\r\n") == b"next\r\n"


@pytest.mark.asyncio
async def test_skip_line_waits_when_all_it_holds_may_be_a_terminator():
    reader, _ = _reader()
    reader.feed(b"\r")
    skipping = await _soon(reader.skip_line(b"\r\n", max_bytes=0))
    assert not skipping.done()
    reader.feed(b"\n")
    assert await skipping == 2


@pytest.mark.asyncio
async def test_skip_line_gives_up_past_its_budget():
    reader, _ = _reader()
    reader.feed(b"a" * 30)
    assert await reader.skip_line(b"\r\n", max_bytes=20) is None


@pytest.mark.asyncio
async def test_a_transport_paused_at_the_switch_to_tls_is_resumed():
    """The TLS layer reads through it: left paused, the session would stall."""
    reader, old = _reader(limit=10)
    reader.feed(b"a" * 30)
    assert old.paused
    new = _Transport()
    reader.replace_transport(new)
    assert not old.paused
    assert not new.paused


@pytest.mark.asyncio
async def test_a_line_of_exactly_the_limit_survives_a_cut_inside_its_terminator():
    """Found in review: with CR and LF in separate segments, the buffer holds
    one byte more than the limit before the line can be seen to end."""
    reader, _ = _reader(limit=10)
    reader.feed(b"a" * 10 + b"\r")
    waiting = await _soon(reader.readline(b"\r\n"))
    assert not waiting.done()
    reader.feed(b"\n")
    assert await waiting == b"a" * 10 + b"\r\n"
    # One byte more is one too many, wherever the cut falls.
    reader.feed(b"a" * 11 + b"\r")
    with pytest.raises(LineTooLong):
        await reader.readline(b"\r\n")


@pytest.mark.asyncio
async def test_readexactly_waits_for_all_of_it():
    reader, _ = _reader()
    reader.feed(b"abc")
    waiting = await _soon(reader.readexactly(5))
    assert not waiting.done()
    reader.feed(b"defg")
    assert await waiting == b"abcde"
    assert await reader.readexactly(2) == b"fg"


@pytest.mark.asyncio
async def test_a_reader_that_falls_behind_pauses_the_transport():
    reader, transport = _reader(limit=10)
    reader.feed(b"a" * 19 + b"\n")
    assert not transport.paused
    reader.feed(b"b")
    # More than twice the limit is buffered.
    assert transport.paused
    await reader.skip_line()
    assert not transport.paused
    assert transport.calls == ["pause", "resume"]


@pytest.mark.asyncio
async def test_waiting_for_more_resumes_a_paused_transport():
    """A read larger than the buffer may hold would otherwise never finish."""
    reader, transport = _reader(limit=4)
    reader.feed(b"a" * 9)
    assert transport.paused
    waiting = await _soon(reader.readexactly(12))
    assert not transport.paused
    reader.feed(b"b" * 3)
    assert await waiting == b"a" * 9 + b"b" * 3


@pytest.mark.asyncio
async def test_after_close_what_is_buffered_is_still_read_then_it_ends():
    reader, _ = _reader()
    reader.feed(b"last\npartial")
    reader.close()
    assert await reader.readline() == b"last\n"
    with pytest.raises(ConnectionClosed):
        await reader.readline()
    with pytest.raises(ConnectionClosed):
        await reader.readexactly(100)
    with pytest.raises(ConnectionClosed):
        await reader.skip_line()


@pytest.mark.asyncio
async def test_close_wakes_a_waiting_reader():
    reader, _ = _reader()
    waiting = await _soon(reader.readline())
    reader.close()
    with pytest.raises(ConnectionClosed):
        await waiting


@pytest.mark.asyncio
async def test_discard_drops_what_was_received_before_tls():
    reader, _ = _reader()
    reader.feed(b"EHLO injected\r\n")
    reader.discard()
    assert not reader.pending()
    replacement = _Transport()
    reader.replace_transport(replacement)
    reader.feed(b"a" * 30)
    # Back-pressure now goes to the new transport.
    assert replacement.paused


@pytest.mark.parametrize(
    ("buffered", "taken", "slow"),
    [
        # Whole lines only: an unfinished one stays.
        (b"a\r\n\r\nb\r\ncc", b"a\r\n\r\nb\r\n", 0),
        # Up to a line that starts with a dot, stuffed or the end of the message.
        (b"a\r\n.b\r\nc\r\n", b"a\r\n", 0),
        (b"a\r\n.\r\n", b"a\r\n", 0),
        (b".\r\n", b"", 0),
        (b"..a\r\nb\r\n", b"", 0),
        # A bare LF or CR among them: none, and how far was looked.
        (b"a\nb\r\n", b"", 5),
        (b"a\rb\r\nc\r\n", b"", 8),
        (b"a\r\n\n", b"", 4),
        # No more than was asked for, so no line longer than that either.
        (b"aaaa\r\nbbbb\r\n", b"aaaa\r\n", 0),
        (b"aaaaaaaaaa\r\n", b"", 0),
        (b"abc", b"", 0),
        (b"", b"", 0),
    ],
)
def test_plain_lines_are_taken_together_and_no_other(buffered, taken, slow):
    reader, _ = _reader(limit=100)
    reader.feed(buffered)
    assert reader.take_plain_lines(8) == (taken, slow)
    assert bytes(reader._buffer) == buffered[len(taken) :]


def test_taking_plain_lines_lets_the_peer_send_again():
    reader, transport = _reader(limit=4096)
    lines = (b"x" * 62 + b"\r\n") * 200
    reader.feed(lines)
    assert transport.paused
    assert reader.take_plain_lines(len(lines)) == (lines, 0)
    assert not transport.paused

"""Receive buffer for one connection.

Everything the peer has sent and the session has not consumed yet sits in one
``bytearray``. The session reads lines and fixed-size blocks out of it; the
transport is paused when the peer sends faster than the session reads.
"""

from __future__ import annotations

import asyncio


class ConnectionClosed(Exception):
    """The peer is gone and the buffer does not hold what was asked for."""


class LineTooLong(Exception):
    """No terminator within the limit. Nothing was consumed."""


class LineReader:
    """Bytes received and not yet consumed, with back-pressure.

    ``limit`` is the longest line ``readline`` returns, terminator excluded.
    At most about twice that is buffered before the transport is paused.
    """

    def __init__(self, transport: asyncio.Transport, limit: int):
        self._transport = transport
        self._limit = limit
        self._buffer = bytearray()
        self._paused = False
        self._closed = False
        self._waiter: asyncio.Future | None = None

    # ------------------------------------------------- fed by the protocol
    def feed(self, data: bytes) -> None:
        self._buffer += data
        self._wake()
        if not self._paused and len(self._buffer) > 2 * self._limit:
            self._transport.pause_reading()
            self._paused = True

    def close(self) -> None:
        """No more bytes will come."""
        self._closed = True
        self._wake()

    def discard(self) -> None:
        """Drop what is buffered. Called right before the switch to TLS.

        RFC 3207 section 4.2. Keeping it would let whoever sits on the path
        have commands read as if they had come through the encrypted channel.
        """
        self._buffer.clear()

    def replace_transport(self, transport: asyncio.Transport) -> None:
        """Apply back-pressure to the TLS transport from now on."""
        if self._paused:
            # The TLS layer reads through the old one: it must not stay paused.
            self._paused = False
            self._transport.resume_reading()
        self._transport = transport

    # ----------------------------------------------------- read by the session
    def pending(self) -> bool:
        """True when the peer has sent bytes nobody has read yet."""
        return bool(self._buffer)

    async def readline(self, terminator: bytes = b"\n", limit: int | None = None) -> bytes:
        """The next line, terminator included.

        Raises ``LineTooLong`` when there is none within ``limit`` bytes; the
        caller then decides whether to ``skip_line`` or hang up.
        """
        limit = self._limit if limit is None else limit
        searched = 0
        while True:
            end = self._buffer.find(terminator, searched)
            # Without a terminator yet, the buffer may end on the start of
            # one: a line of exactly the limit is not too long for that.
            if end > limit or (end < 0 and len(self._buffer) >= limit + len(terminator)):
                raise LineTooLong
            if end >= 0:
                return self._take(end + len(terminator))
            # The terminator may be cut in two by the end of the buffer.
            searched = max(0, len(self._buffer) - len(terminator) + 1)
            await self._more()

    async def skip_line(
        self, terminator: bytes = b"\n", max_bytes: int | None = None
    ) -> int | None:
        """Discard everything up to and including the next terminator.

        Returns how many bytes that was. Returns None, having stopped, once
        more than ``max_bytes`` were discarded without finding one.
        """
        discarded = 0
        while True:
            end = self._buffer.find(terminator)
            if end >= 0:
                self._take(end + len(terminator))
                return discarded + end + len(terminator)
            # Keep what could be the start of a terminator.
            droppable = len(self._buffer) - (len(terminator) - 1)
            if droppable > 0:
                discarded += droppable
                self._take(droppable)
            if max_bytes is not None and discarded > max_bytes:
                return None
            await self._more()

    async def readexactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            await self._more()
        return self._take(count)

    # ---------------------------------------------------------------- internals
    def _take(self, count: int) -> bytes:
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        if self._paused and len(self._buffer) <= self._limit:
            self._paused = False
            self._transport.resume_reading()
        return data

    async def _more(self) -> None:
        """Wait until ``feed`` or ``close`` is called."""
        if self._closed:
            raise ConnectionClosed
        if self._paused:
            # The buffer is full of something the caller is not taking, a
            # line with no end. It gets more only if reading resumes.
            self._paused = False
            self._transport.resume_reading()
        self._waiter = asyncio.get_running_loop().create_future()
        try:
            await self._waiter
        finally:
            self._waiter = None

    def _wake(self) -> None:
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

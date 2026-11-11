"""Unit tests for :class:`pymta.limits.IPGate`.

Unlike the rest of the suite in this directory, these tests do NOT need a
running MTA — they exercise the gate object directly. They run via the same
``test-mta-in-py`` target but skip the SMTP integration fixtures.
"""

from __future__ import annotations

import pytest

from pymta.limits import IPGate, LogThrottle, TooManyConnections

# ---------------------------------------------------------------------------
# LogThrottle: one line per key per interval, over a bounded set of keys.
# ---------------------------------------------------------------------------


def test_throttle_passes_one_event_per_key_per_interval():
    throttle = LogThrottle(interval=60, max_keys=10)
    assert throttle.allow("192.0.2.1", now=0) == 0
    assert throttle.allow("192.0.2.1", now=1) is None
    assert throttle.allow("192.0.2.1", now=59) is None
    # Another address has its own interval.
    assert throttle.allow("192.0.2.2", now=2) == 0
    # The next pass reports what was held back.
    assert throttle.allow("192.0.2.1", now=60) == 2
    assert throttle.allow("192.0.2.1", now=61) is None


def test_throttle_holds_new_keys_once_full():
    throttle = LogThrottle(interval=60, max_keys=2)
    assert throttle.allow("192.0.2.1", now=0) == 0
    assert throttle.allow("192.0.2.2", now=0) == 0
    assert throttle.allow("192.0.2.3", now=1) is None
    assert len(throttle._seen) == 2


def test_throttle_makes_room_by_dropping_expired_keys():
    throttle = LogThrottle(interval=60, max_keys=2)
    assert throttle.allow("192.0.2.1", now=0) == 0
    assert throttle.allow("192.0.2.2", now=30) == 0
    assert throttle.allow("192.0.2.3", now=70) == 0
    assert set(throttle._seen) == {"192.0.2.2", "192.0.2.3"}


# ---------------------------------------------------------------------------
# Concurrent-cap behaviour (existing semantics).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_cap_blocks_when_total_reached():
    gate = IPGate(max_total=2)
    await gate._try_acquire("1.1.1.1")
    await gate._try_acquire("2.2.2.2")
    with pytest.raises(TooManyConnections) as exc:
        await gate._try_acquire("3.3.3.3")
    assert exc.value.scope == "global"
    await gate._release("1.1.1.1")
    await gate._release("2.2.2.2")


@pytest.mark.asyncio
async def test_per_ip_cap_blocks_same_ip_only():
    gate = IPGate(max_total=5)
    await gate._try_acquire("1.1.1.1")
    await gate._try_acquire("1.1.1.1")
    with pytest.raises(TooManyConnections) as exc:
        await gate._try_acquire("1.1.1.1")
    assert exc.value.scope == "per_ip"
    # A different IP is still admitted.
    await gate._try_acquire("2.2.2.2")
    await gate._release("1.1.1.1")
    await gate._release("1.1.1.1")
    await gate._release("2.2.2.2")


@pytest.mark.asyncio
async def test_release_frees_slot_for_same_ip():
    gate = IPGate(max_total=2)
    await gate._try_acquire("1.1.1.1")
    with pytest.raises(TooManyConnections):
        await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    # Slot freed — next acquire from the same IP succeeds.
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")


@pytest.mark.asyncio
async def test_zero_disables_concurrency_caps():
    gate = IPGate(max_total=0)
    # Loopback test harness traffic comes from one IP; we must not throttle it.
    for _ in range(50):
        await gate._try_acquire("127.0.0.1")
    # Not just "did not raise": disabling the caps must not disable the
    # bookkeeping, or the metrics and the release path go wrong with it.
    assert gate._total == 50
    assert gate._per_ip["127.0.0.1"] == 50
    for _ in range(50):
        await gate._release("127.0.0.1")
    assert gate._total == 0
    assert "127.0.0.1" not in gate._per_ip

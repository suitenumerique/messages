"""Unit tests for :class:`pymta.limits.IPGate`.

Unlike the rest of the suite in this directory, these tests do NOT need a
running MTA — they exercise the gate object directly. They run via the same
``test-mta-in-py`` target but skip the SMTP integration fixtures.
"""

from __future__ import annotations

import pytest

from pymta.limits import IPGate, TooManyConnections


class _FakeClock:
    """Manually-advanced monotonic clock for deterministic rate-window tests."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Concurrent-cap behaviour (existing semantics).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_cap_blocks_when_total_reached():
    gate = IPGate(max_total=2, max_per_ip=0)
    await gate._try_acquire("1.1.1.1")
    await gate._try_acquire("2.2.2.2")
    with pytest.raises(TooManyConnections) as exc:
        await gate._try_acquire("3.3.3.3")
    assert exc.value.scope == "global"
    await gate._release("1.1.1.1")
    await gate._release("2.2.2.2")


@pytest.mark.asyncio
async def test_per_ip_cap_blocks_same_ip_only():
    gate = IPGate(max_total=0, max_per_ip=2)
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
    gate = IPGate(max_total=0, max_per_ip=1)
    await gate._try_acquire("1.1.1.1")
    with pytest.raises(TooManyConnections):
        await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    # Slot freed — next acquire from the same IP succeeds.
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")


@pytest.mark.asyncio
async def test_zero_disables_concurrency_caps():
    gate = IPGate(max_total=0, max_per_ip=0)
    # Loopback test harness traffic comes from one IP; we must not throttle it.
    for _ in range(50):
        await gate._try_acquire("127.0.0.1")
    for _ in range(50):
        await gate._release("127.0.0.1")


@pytest.mark.asyncio
async def test_rate_cap_blocks_after_quota_in_window():
    clock = _FakeClock()
    gate = IPGate(max_total=0, max_per_ip=0, max_per_ip_per_minute=3, clock=clock)
    # Three quick session acquires from one IP — all release immediately so the
    # concurrent cap can't be the thing blocking us; only the rate cap is.
    for _ in range(3):
        await gate._try_acquire("1.1.1.1")
        await gate._release("1.1.1.1")
    with pytest.raises(TooManyConnections) as exc:
        await gate._try_acquire("1.1.1.1")
    assert exc.value.scope == "per_ip_rate"


@pytest.mark.asyncio
async def test_rate_window_resets_after_60_seconds():
    clock = _FakeClock()
    gate = IPGate(max_total=0, max_per_ip=0, max_per_ip_per_minute=2, clock=clock)
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    with pytest.raises(TooManyConnections):
        await gate._try_acquire("1.1.1.1")
    # Window closes — fresh budget.
    clock.advance(60.1)
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    await gate._try_acquire("1.1.1.1")


@pytest.mark.asyncio
async def test_rate_cap_is_per_ip_not_global():
    clock = _FakeClock()
    gate = IPGate(max_total=0, max_per_ip=0, max_per_ip_per_minute=2, clock=clock)
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    await gate._try_acquire("1.1.1.1")
    await gate._release("1.1.1.1")
    # A second IP gets its own bucket — must not be tarred by 1.1.1.1's spend.
    await gate._try_acquire("2.2.2.2")
    await gate._release("2.2.2.2")


@pytest.mark.asyncio
async def test_rate_cap_disabled_when_zero():
    clock = _FakeClock()
    gate = IPGate(max_total=0, max_per_ip=0, max_per_ip_per_minute=0, clock=clock)
    for _ in range(50):
        await gate._try_acquire("1.1.1.1")
        await gate._release("1.1.1.1")
    # And the rate-tracking dict stays empty so loopback dev/test isn't
    # paying memory for a feature it never uses.
    assert gate._rate_per_ip == {}


@pytest.mark.asyncio
async def test_rate_dict_prunes_expired_entries():
    """The rate map must not grow without bound under churning client IPs."""
    clock = _FakeClock()
    from pymta import limits

    # Shrink the prune interval so the test doesn't have to call 1000 times.
    original = limits._RATE_PRUNE_EVERY
    limits._RATE_PRUNE_EVERY = 10
    try:
        gate = IPGate(max_total=0, max_per_ip=0, max_per_ip_per_minute=1, clock=clock)
        for i in range(9):
            await gate._try_acquire(f"10.0.0.{i}")
            await gate._release(f"10.0.0.{i}")
        assert len(gate._rate_per_ip) == 9
        # All previous windows expire.
        clock.advance(61.0)
        # The 10th acquire triggers the prune sweep.
        await gate._try_acquire("10.0.0.99")
        await gate._release("10.0.0.99")
        # Only the most recent entry survives; all stale ones are gone.
        assert set(gate._rate_per_ip.keys()) == {"10.0.0.99"}
    finally:
        limits._RATE_PRUNE_EVERY = original


# ---------------------------------------------------------------------------
# Rate cap interacts cleanly with the concurrent caps.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_cap_takes_precedence_over_rate_cap():
    # When both would fire, the cheaper-to-evaluate global check should win
    # so we don't bother accounting against the rate bucket for a session we
    # were never going to admit anyway.
    clock = _FakeClock()
    gate = IPGate(max_total=1, max_per_ip=0, max_per_ip_per_minute=10, clock=clock)
    await gate._try_acquire("1.1.1.1")
    with pytest.raises(TooManyConnections) as exc:
        await gate._try_acquire("2.2.2.2")
    assert exc.value.scope == "global"
    # The refused acquire must not have consumed 2.2.2.2's rate budget.
    assert "2.2.2.2" not in gate._rate_per_ip
    await gate._release("1.1.1.1")


@pytest.mark.asyncio
async def test_per_ip_concurrent_cap_takes_precedence_over_rate_cap():
    clock = _FakeClock()
    gate = IPGate(max_total=0, max_per_ip=1, max_per_ip_per_minute=10, clock=clock)
    await gate._try_acquire("1.1.1.1")
    with pytest.raises(TooManyConnections) as exc:
        await gate._try_acquire("1.1.1.1")
    assert exc.value.scope == "per_ip"
    # Only the first (admitted) acquire should have been billed to the bucket.
    assert gate._rate_per_ip["1.1.1.1"][0] == 1
    await gate._release("1.1.1.1")

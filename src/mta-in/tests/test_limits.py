"""Unit tests for :class:`pymta.limits.IPGate`.

Unlike the rest of the suite in this directory, these tests do NOT need a
running MTA — they exercise the gate object directly. They run via the same
``test-mta-in-py`` target but skip the SMTP integration fixtures.
"""

from __future__ import annotations

import pytest

from pymta.limits import IPGate, TooManyConnections

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

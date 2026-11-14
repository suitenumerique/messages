"""Fuzzing the session gate, with slots taken back from sources over their share.

Sessions arrive and leave in any order. An arrival does what
``SMTPConnection._admit`` does, every session being idle: the worst case for
the sources that hold the slots.

Run with: make fuzz-mta-in-py
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pymta.limits import IPGate, TooManyConnections

from tests.fuzzing import fuzz_settings

pytestmark = pytest.mark.fuzz

_SOURCES = [f"198.51.100.{n}" for n in range(8)]
# An arrival from a source, or the departure of one of its sessions.
_EVENTS = st.lists(st.tuples(st.booleans(), st.sampled_from(_SOURCES)), max_size=120)


@settings(**fuzz_settings())
@given(
    slots=st.integers(min_value=1, max_value=24),
    fraction=st.sampled_from([0.1, 0.25, 0.5, 1]),
    events=_EVENTS,
)
def test_slots_are_never_lost_nor_overdrawn_and_taking_one_back_is_fair(slots, fraction, events):
    gate = IPGate(max_total=slots, max_share=fraction)
    held = dict.fromkeys(_SOURCES, 0)

    for arrives, source in events:
        if not arrives:
            if held[source]:
                gate.release(source)
                held[source] -= 1
        else:
            try:
                gate.acquire(source)
            except TooManyConnections as refused:
                crowding = gate.crowding_sources(source)
                if refused.scope != "global" or not crowding:
                    continue
                giver = crowding[0]
                # From a source that holds more, and at least two more: the
                # exchange cannot be undone by the one that gave.
                assert held[giver] >= held[source] + 2
                assert held[giver] == max(held.values())
                gate.release(giver)
                held[giver] -= 1
                # Never raises: the slot is there and the newcomer is under
                # its share.
                gate.acquire(source)
                assert gate.crowding_sources(giver) == []
            held[source] += 1

        assert gate._total == sum(held.values()) <= slots
        assert gate._per_ip == {source: count for source, count in held.items() if count}

    for source, count in held.items():
        for _ in range(count):
            gate.release(source)
    assert gate._total == 0 and gate._per_ip == {}

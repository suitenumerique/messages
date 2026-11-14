"""Shared settings for the Hypothesis fuzz suite (``test_*_fuzz.py``).

Excluded from the default selection. Run with ``make fuzz-mta-in-py``, or
``make fuzz-mta-in-py-intensive`` for the long soak.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck

# FUZZ_EXAMPLES=20000 for a deeper run.
EXAMPLES = int(os.environ.get("FUZZ_EXAMPLES", "2000"))


def fuzz_settings(divisor: int = 1) -> dict:
    """Settings for one test. ``divisor`` scales down the slow ones.

    Phases are Hypothesis's defaults: shrinking and replay of stored failures
    only cost anything once a failure exists.
    """
    return {
        "max_examples": max(EXAMPLES // divisor, 10),
        "deadline": None,
        "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    }

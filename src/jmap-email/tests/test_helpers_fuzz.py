"""
Fuzzing tests for the null-safe shape accessors and the msg-id validator.

:mod:`jmap_email.helpers` promises that every accessor "returns a
sensible default on absence; none of them ever raises". That is a
property, and it had no property test — four of the accessors raised
``AttributeError`` on ``None``, the very value :func:`parse_email`
returns for input it cannot parse.

Run with: pytest -m fuzz tests/test_helpers_fuzz.py
Or: make fuzz-jmap-email
"""

import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import jmap_email
from jmap_email import is_valid_msg_id

FUZZ_SETTINGS = {
    # Override for a deeper soak: FUZZ_EXAMPLES=100000 make fuzz-jmap-email
    "max_examples": int(os.environ.get("FUZZ_EXAMPLES", "10000")),
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
    # Phases are Hypothesis's defaults on purpose. ``shrink`` and
    # ``explain`` cost nothing on a green run — they only engage once a
    # failure exists, which is exactly when you want a minimal example
    # rather than the raw generated blob. ``reuse`` replays a stored
    # failure until it is fixed, which is what makes an intermittent
    # find reproducible; it needs ``.hypothesis`` to survive the
    # container, so compose mounts it.
}

# Arbitrary junk in the first-argument position: the accessors are the
# library's answer to "stop writing `parsed.get(x) or []`", so they are
# exactly what a caller reaches for before checking anything.
junk = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=True)
    | st.text(max_size=20)
    | st.binary(max_size=20),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=8,
)

# Accessors taking (parsed_email, name) and (parsed_email,) respectively.
NAMED = ["find_header", "find_headers", "has_header"]
SHAPE = [
    "first_address",
    "first_address_email",
    "first_address_name",
    "first_msgid",
    "msgid_chain",
    "sent_at_to_datetime",
]


@pytest.mark.fuzz
class TestHelpersNeverRaise:
    """The documented guarantee, as a property."""

    @settings(**FUZZ_SETTINGS)
    @given(value=junk, name=st.text(max_size=12))
    def test_header_accessors(self, value, name):
        for fn_name in NAMED:
            getattr(jmap_email, fn_name)(value, name)

    @settings(**FUZZ_SETTINGS)
    @given(value=junk)
    def test_shape_accessors(self, value):
        for fn_name in SHAPE:
            getattr(jmap_email, fn_name)(value)

    @settings(**FUZZ_SETTINGS)
    @given(value=junk, key=st.sampled_from(["textBody", "htmlBody", "nope"]))
    def test_body_accessors(self, value, key):
        jmap_email.body_text_joined(value, key)
        jmap_email.body_part_text(value, value)

    @settings(**FUZZ_SETTINGS)
    @given(value=junk)
    def test_body_part_text_with_arbitrary_part(self, value):
        jmap_email.body_part_text({"bodyValues": {"1": {"value": "x"}}}, value)


@pytest.mark.fuzz
class TestIsValidMsgIdFuzz:
    """``True`` must mean "usable exactly as given"."""

    @settings(**FUZZ_SETTINGS)
    @given(value=junk)
    def test_never_raises_and_returns_bool(self, value):
        assert isinstance(is_valid_msg_id(value), bool)

    @settings(**FUZZ_SETTINGS)
    @given(
        value=st.text(max_size=120)
        | st.builds(
            lambda a, b: f"<{a}@{b}>", st.text(max_size=30), st.text(max_size=30)
        )
    )
    def test_accepted_ids_need_no_cleaning(self, value):
        """A caller keeps the raw string it validated, so an accepted value
        must already be free of anything the composer would strip on the
        way out — otherwise ``True`` hands back an injection payload."""
        # pylint: disable=protected-access
        from jmap_email.composer import _sanitize_header_value

        if is_valid_msg_id(value):
            assert _sanitize_header_value(value) == value
            assert "\r" not in value and "\n" not in value

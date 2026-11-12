"""Fuzzing the enveloped deliver body reader.

The body is ``<envelope JSON>\\n<message>``. It is authenticated (the token's
``body_hash`` covers it), so this is not an attack surface, but a reader that
raised on an odd envelope would turn into a 500 and a message deferred until
the sender gives up.

Run with: make fuzz-back core/tests/api/test_inbound_mta_fuzz.py
"""

import datetime
import email
import email.utils
import json
import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from core.api.viewsets.inbound.mta import (
    _is_recipient_list,
    _received_header,
    _split_enveloped_body,
)

pytestmark = pytest.mark.fuzz

FUZZ_SETTINGS = {
    "max_examples": int(os.environ.get("FUZZ_EXAMPLES", "2000")),
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow],
}

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=8,
)


@settings(**FUZZ_SETTINGS)
@given(body=st.binary(max_size=200))
def test_any_body_splits_or_is_refused(body):
    """Never an exception, whatever the bytes."""
    split = _split_enveloped_body(body)
    if split is not None:
        envelope, message = split
        assert isinstance(envelope, dict)
        assert body.endswith(message)


@settings(**FUZZ_SETTINGS)
@given(
    envelope=st.dictionaries(st.text(max_size=8), json_values, max_size=5),
    message=st.binary(max_size=200),
    ascii_only=st.booleans(),
)
def test_an_envelope_and_its_message_come_back_as_sent(envelope, message, ascii_only):
    """Whatever the message holds, including newlines on its first bytes."""
    head = json.dumps(envelope, ensure_ascii=ascii_only).encode("utf-8")
    assert _split_enveloped_body(head + b"\n" + message) == (envelope, message)


@settings(**FUZZ_SETTINGS)
@given(value=json_values)
def test_only_a_list_of_strings_is_a_recipient_list(value):
    """Any JSON value: accepted only as a list of strings."""
    expected = isinstance(value, list) and all(isinstance(item, str) for item in value)
    assert _is_recipient_list(value) is expected


TRACE_KEYS = [
    "client_helo",
    "client_hostname",
    "client_address",
    "protocol",
    "tls_version",
    "tls_cipher",
    "server_name",
    "mta_session",
]
NOW = datetime.datetime(2026, 11, 11, 15, 4, 5, tzinfo=datetime.timezone.utc)

trace_values = st.one_of(
    json_values,
    st.text(max_size=300),
    st.text(
        alphabet=st.sampled_from(list('ab.:;()[]<>"\\ \t\r\n\x00=,@')), max_size=40
    ),
    st.ip_addresses().map(str),
    st.sampled_from(
        ["ESMTP", "ESMTPS", "TLSv1.3", "TLS_AES_256_GCM_SHA384", "mx.a.example"]
    ),
)


@settings(**FUZZ_SETTINGS)
@given(metadata=st.dictionaries(st.sampled_from(TRACE_KEYS), trace_values, max_size=8))
def test_the_trace_header_is_one_well_formed_header_whatever_the_mta_sends(metadata):
    """It is written into every stored message, from fields the peer chose
    (HELO) or that another service sent: none may add a line or a header."""
    header = _received_header(metadata, NOW)

    assert header.startswith("Received: from ") and header.endswith("\r\n")
    assert len(header) < 1000 and "\x00" not in header
    lines = header[:-2].split("\r\n")
    assert 2 <= len(lines) <= 3
    assert all(line.startswith("\t") for line in lines[1:])
    assert "\r" not in "".join(lines) and "\n" not in "".join(lines)

    # Parsed with what follows it, it is one header and the next one is intact.
    parsed = email.message_from_string(header + "Subject: next\r\n\r\nbody\r\n")
    assert parsed.keys() == ["Received", "Subject"]
    assert parsed["Subject"] == "next"
    value, _, date = " ".join(parsed["Received"].split()).rpartition(";")
    assert ";" not in value
    assert email.utils.parsedate_to_datetime(date.strip()) == NOW

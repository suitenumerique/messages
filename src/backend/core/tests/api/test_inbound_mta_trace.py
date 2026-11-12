"""What the ``Received`` header written at ingest changes for trust decisions.

``headers_blocks`` gives the spam rules and the ``Authentication-Results``
reader the headers above the first ``Received``. Before the MTA endpoint
always wrote one of its own, a message from pymta had none of ours, and that
first block was whatever the sender had put on top of their message.
"""

import datetime
import hashlib
import json
from unittest.mock import patch

from django.conf import settings

import jwt
import pytest
from jmap_email import parse_email
from rest_framework.test import APIClient

from core.api.viewsets.inbound.mta import ENVELOPED_CONTENT_TYPE
from core.mda.spam import check_hardcoded_rules
from core.mda.utils import headers_blocks

FORGED = (
    b"X-Spam: Ham\r\n"
    b"Authentication-Results: ours.example; dkim=pass header.d=bank.example\r\n"
    b"Received: from forged.example (forged.example [192.0.2.66]) by forged.example;"
    b" Mon, 1 Jan 2024 00:00:00 +0000\r\n"
    b"From: ceo@bank.example\r\nTo: rcpt@example.com\r\nSubject: hello\r\n"
    b"Message-ID: <forged@bank.example>\r\n\r\nbody\r\n"
)


def _ingest(raw, envelope):
    """Post ``raw`` as pymta does. Returns the bytes handed on for storage."""
    body = json.dumps(envelope).encode() + b"\n" + raw
    token = jwt.encode(
        {
            "body_hash": hashlib.sha256(body).hexdigest(),
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30),
            "mta_session": "9bf92de8",
        },
        settings.MDA_API_SECRET,
        algorithm="HS256",
    )
    with patch(
        "core.api.viewsets.inbound.mta.deliver_inbound_message", return_value=True
    ) as deliver:
        response = APIClient().post(
            "/api/v1.0/inbound/mta/deliver/",
            data=body,
            content_type=ENVELOPED_CONTENT_TYPE,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
    assert response.status_code == 200, response.content
    return deliver.call_args[0][2]


@pytest.fixture(name="stored")
def fixture_stored():
    """A message whose sender put trusted-looking headers on top, as pymta
    delivers it: no reverse name, which used to mean no ``Received`` of ours."""
    return _ingest(
        FORGED,
        {
            "sender": "ceo@bank.example",
            "original_recipients": ["rcpt@example.com"],
            "client_address": "203.0.113.9",
            "client_hostname": None,
            "client_helo": "mx.sender.example",
            "protocol": "ESMTP",
            "tls_version": None,
            "tls_cipher": None,
            "server_name": "mx1.ours.example",
        },
    )


def test_our_trace_is_on_top_of_everything_the_sender_sent(stored):
    """Return-Path, then our Received, then the message untouched."""
    assert stored.startswith(
        b"Return-Path: <ceo@bank.example>\r\n"
        b"Received: from mx.sender.example ([203.0.113.9])\r\n"
        b"\tby mx1.ours.example with ESMTP id 9bf92de8;\r\n\t"
    )
    assert stored.endswith(FORGED)


def test_the_first_trust_block_holds_only_what_we_wrote(stored):
    """Block 0 is ours; the sender's top headers are in block 1."""
    blocks = headers_blocks(parse_email(stored))

    assert set(blocks[0]) == {"return-path", "received"}
    assert "mx1.ours.example" in blocks[0]["received"][0]
    # The sender's own top headers are one hop further, with their Received.
    assert {"x-spam", "authentication-results", "received"} <= set(blocks[1])
    assert "forged.example" in blocks[1]["received"][0]


def test_a_header_rule_cannot_be_satisfied_by_the_sender(stored):
    """``action: ham`` on a header match, at the default ``trusted_relays``."""
    parsed = parse_email(stored)
    rule = {"rules": [{"header_match": "X-Spam: Ham", "action": "ham"}]}

    assert check_hardcoded_rules(parsed, rule) is None
    # Trusting one relay in front of us is an explicit choice, and then it does.
    assert check_hardcoded_rules(parsed, {**rule, "trusted_relays": 1}) == "ham"


def test_without_our_trace_the_sender_supplied_the_first_block():
    """What the same message looked like to the rules before: the reason for
    the header. Kept as the counterpart of the tests above."""
    before = b"Return-Path: <ceo@bank.example>\r\n" + FORGED
    blocks = headers_blocks(parse_email(before))
    assert {"x-spam", "authentication-results"} <= set(blocks[0])
    rule = {"rules": [{"header_match": "X-Spam: Ham", "action": "ham"}]}
    assert check_hardcoded_rules(parse_email(before), rule) == "ham"

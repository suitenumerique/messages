"""Startup configuration checks in :mod:`pymta.server`.

There are exactly two supported topologies:

* PROXY protocol on, behind a balancer named in ``PYMTA_TRUSTED_PROXIES``;
* PROXY protocol off, exposed directly.

A balancer *without* PROXY protocol is not supported, because pymta would
attribute every session to the balancer's own IP. PROXY protocol without an
allowlist is refused too, since enabling it is the claim that a known balancer
sits in front.
"""

from __future__ import annotations

import importlib
from ipaddress import ip_network

import pytest

from pymta import settings
from pymta.server import _check_proxy_trust_config


def test_proxy_protocol_without_allowlist_refuses_to_start(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])
    with pytest.raises(RuntimeError, match="PYMTA_TRUSTED_PROXIES"):
        _check_proxy_trust_config()


def test_proxy_protocol_with_allowlist_starts(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(
        settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")]
    )
    _check_proxy_trust_config()


def test_allowlist_is_irrelevant_without_proxy_protocol(monkeypatch):
    # Direct exposure: the wire peer is the client, no header is parsed.
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", False)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])
    _check_proxy_trust_config()


@pytest.mark.parametrize(
    "env, enabled",
    [
        ("true", True),
        ("false", False),
        (None, False),
    ],
)
def test_proxy_protocol_reads_only_the_pymta_name(monkeypatch, env, enabled):
    # The Postfix image drives the same feature from its own
    # ENABLE_PROXY_PROTOCOL=haproxy; pymta must not inherit it, so the two
    # services can share an env file.
    monkeypatch.setenv("ENABLE_PROXY_PROTOCOL", "haproxy")
    if env is None:
        monkeypatch.delenv("PYMTA_ENABLE_PROXY_PROTOCOL", raising=False)
    else:
        monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", env)
    try:
        assert importlib.reload(settings).PYMTA_ENABLE_PROXY_PROTOCOL is enabled
    finally:
        monkeypatch.undo()
        importlib.reload(settings)

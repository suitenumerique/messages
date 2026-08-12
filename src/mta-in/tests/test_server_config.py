"""Startup configuration checks in :mod:`pymta.server`.

There are exactly two supported topologies:

* PROXY protocol on, behind a balancer named in ``PYMTA_TRUSTED_PROXIES``;
* PROXY protocol off, exposed directly.

A balancer *without* PROXY protocol is not supported, because pymta would
attribute every session to the balancer's own IP. PROXY protocol without an
allowlist starts, but only behind a loud warning: nothing is left to filter
headers on, so the network isolation carries the whole trust boundary.
"""

from __future__ import annotations

import importlib
import logging
from ipaddress import ip_network

import pytest

from pymta import settings
from pymta.server import _check_proxy_trust_config


def test_proxy_protocol_without_allowlist_starts_with_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    assert "SECURITY" in caplog.text
    assert "PYMTA_TRUSTED_PROXIES is empty" in caplog.text


def test_proxy_protocol_with_allowlist_starts_without_warning(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network("10.89.0.0/24")])
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    assert caplog.records == []


@pytest.mark.parametrize("catch_all", ["0.0.0.0/0", "::/0"])
def test_proxy_protocol_with_catch_all_allowlist_warns(monkeypatch, caplog, catch_all):
    # Non-empty, so it clears the emptiness check, while trusting every peer
    # exactly as much as no allowlist would: same posture, same warning.
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network(catch_all)])
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    assert "matches every peer" in caplog.text


def test_catch_all_warns_even_beside_a_real_network(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(
        settings,
        "PYMTA_TRUSTED_PROXIES",
        [ip_network("10.89.0.0/24"), ip_network("0.0.0.0/0")],
    )
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    assert "matches every peer" in caplog.text


def test_catch_all_is_irrelevant_without_proxy_protocol(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", False)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [ip_network("0.0.0.0/0")])
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    assert caplog.records == []


def test_allowlist_is_irrelevant_without_proxy_protocol(monkeypatch, caplog):
    # Direct exposure: the wire peer is the client, no header is parsed.
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", False)
    monkeypatch.setattr(settings, "PYMTA_TRUSTED_PROXIES", [])
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    assert caplog.records == []


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

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

import asyncio
import importlib
import inspect
import logging
from ipaddress import ip_network

import pytest

from pymta import runtime, server, settings
from pymta.server import _check_proxy_trust_config, _log_unhandled


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
    record = caplog.records[-1]
    assert record.message == "proxy_trust_unrestricted"
    assert "matches every peer" in record.detail


def test_catch_all_warns_even_beside_a_real_network(monkeypatch, caplog):
    monkeypatch.setattr(settings, "PYMTA_ENABLE_PROXY_PROTOCOL", True)
    monkeypatch.setattr(
        settings,
        "PYMTA_TRUSTED_PROXIES",
        [ip_network("10.89.0.0/24"), ip_network("0.0.0.0/0")],
    )
    with caplog.at_level(logging.WARNING):
        _check_proxy_trust_config()
    record = caplog.records[-1]
    assert record.message == "proxy_trust_unrestricted"
    assert "matches every peer" in record.detail


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


@pytest.mark.parametrize("env, enabled", [("true", True), ("haproxy", True), ("false", False)])
def test_proxy_protocol_prefers_its_own_name(monkeypatch, env, enabled):
    """The prefixed name decides, whatever the Postfix one says."""
    monkeypatch.setenv("ENABLE_PROXY_PROTOCOL", "haproxy")
    monkeypatch.setenv("PYMTA_TRUSTED_PROXIES", "192.0.2.10")
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", env)
    try:
        assert importlib.reload(settings).PYMTA_ENABLE_PROXY_PROTOCOL is enabled
    finally:
        monkeypatch.undo()
        importlib.reload(settings)


def test_the_postfix_name_still_works_and_is_reported(monkeypatch):
    """The old name keeps working so one env file can drive both images.

    Recorded rather than silent: startup names it and says what to set instead,
    which is what turns a switchover into a migration that finishes.
    """
    monkeypatch.setenv("ENABLE_PROXY_PROTOCOL", "haproxy")
    monkeypatch.setenv("PYMTA_TRUSTED_PROXIES", "192.0.2.10")
    monkeypatch.delenv("PYMTA_ENABLE_PROXY_PROTOCOL", raising=False)
    try:
        reloaded = importlib.reload(settings)
        assert reloaded.PYMTA_ENABLE_PROXY_PROTOCOL is True
        assert ("ENABLE_PROXY_PROTOCOL", "PYMTA_ENABLE_PROXY_PROTOCOL") in reloaded.LEGACY_IN_USE
    finally:
        monkeypatch.undo()
        importlib.reload(settings)


def test_the_prefixed_name_wins_and_is_not_reported(monkeypatch):
    monkeypatch.setenv("ENABLE_PROXY_PROTOCOL", "haproxy")
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", "false")
    try:
        reloaded = importlib.reload(settings)
        assert reloaded.PYMTA_ENABLE_PROXY_PROTOCOL is False
        assert reloaded.LEGACY_IN_USE == set()
    finally:
        monkeypatch.undo()
        importlib.reload(settings)


@pytest.mark.asyncio
async def test_an_exception_nobody_awaits_reaches_our_log(caplog):
    """asyncio reports these on its own logger, which our output filters out."""

    def boom():
        raise RuntimeError("timer bug")

    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(_log_unhandled)
    try:
        with caplog.at_level(logging.ERROR, logger="pymta.server"):
            loop.call_soon(boom)
            await asyncio.sleep(0.05)
    finally:
        loop.set_exception_handler(previous)

    (record,) = [r for r in caplog.records if r.name == "pymta.server"]
    assert record.getMessage() == "unhandled_error"
    assert record.exc_info[0] is RuntimeError
    assert "Exception in callback" in record.detail


def test_the_server_installs_the_unhandled_exception_logger():
    assert "set_exception_handler(_log_unhandled)" in inspect.getsource(server._serve)


def _events(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "pymta.server"]


def test_a_successful_tls_reload_is_logged_with_the_new_expiry(monkeypatch, caplog):
    class _Reloaded:
        def reload_tls(self):
            return True

    monkeypatch.setattr(
        server, "publish_certificate_expiry", lambda: {"/certs/a.crt": 4102444800.0}
    )
    with caplog.at_level(logging.INFO, logger="pymta.server"):
        server._reload_tls(_Reloaded())

    assert _events(caplog) == ["tls_reloaded", "tls_certificate"]
    certificate = caplog.records[-1]
    assert certificate.file == "/certs/a.crt"
    assert certificate.not_after == "2100-01-01T00:00:00Z"


def test_a_second_sigterm_does_not_cut_the_drain_short(caplog):
    runtime.reset_for_tests()
    stop = asyncio.Event()
    try:
        with caplog.at_level(logging.INFO, logger="pymta.server"):
            server._on_terminate(server.signal.SIGTERM, stop)
            assert stop.is_set()
            stop.clear()
            server._on_terminate(server.signal.SIGTERM, stop)
        # The second signal neither restarts the drain nor ends it.
        assert not stop.is_set()
        assert _events(caplog) == ["shutdown_started", "shutdown_already_in_progress"]
    finally:
        runtime.reset_for_tests()


def test_a_startup_failure_is_logged_in_our_format_before_the_process_dies(monkeypatch, caplog):
    async def _fails():
        raise OSError("address already in use")

    monkeypatch.setattr(server, "_configure_logging", lambda: None)
    monkeypatch.setattr(server.metrics, "start_metrics_server", lambda *args: None)
    monkeypatch.setattr(server, "_export_config_limits", lambda: None)
    monkeypatch.setattr(server, "_serve", _fails)
    with caplog.at_level(logging.ERROR, logger="pymta.server"), pytest.raises(OSError):
        server.main()

    (record,) = [r for r in caplog.records if r.name == "pymta.server"]
    assert record.getMessage() == "fatal_error"
    assert record.exc_info[0] is OSError

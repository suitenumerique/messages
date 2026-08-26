"""Prometheus metrics tests — pymta-only.

Skipped automatically when ``MTA_METRICS_URL`` is not set (i.e. when running
against the Postfix implementation, which has no Prometheus endpoint).
"""

import logging
import smtplib
import socket
import threading
import time
import urllib.request
from email.mime.text import MIMEText

import pytest

from pymta.metrics import start_metrics_server

logger = logging.getLogger(__name__)


def _scrape(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _metric_value(scrape_text: str, prefix: str) -> float:
    """Sum every series that begins with ``prefix``, return the total.

    A ``prefix`` like ``pymta_messages_total{result="delivered"}`` matches a
    single series. ``pymta_messages_total`` (no label selector) matches
    every series of that metric.
    """
    total = 0.0
    for line in scrape_text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        # Format: `name{labels} value [timestamp]`  or  `name value`
        if line.startswith(prefix):
            parts = line.split()
            # 2 tokens → name value; 3+ tokens → name value timestamp.
            value = parts[-2] if len(parts) >= 3 else parts[-1]
            try:
                total += float(value)
            except ValueError:
                continue
    return total


@pytest.fixture
def metrics_url(mta_metrics_url):
    if not mta_metrics_url:
        pytest.skip("MTA_METRICS_URL not set (only the pymta image exposes metrics)")
    return mta_metrics_url


def test_metrics_endpoint_reachable(metrics_url):
    text = _scrape(metrics_url)
    assert "pymta_connections_total" in text
    assert "pymta_messages_total" in text


def test_delivery_increments_messages_total(metrics_url, mock_api_server, smtp_client):
    mock_api_server.add_mailbox("metrics-test@example.com")

    before = _metric_value(_scrape(metrics_url), 'pymta_messages_total{result="delivered"}')

    msg = MIMEText("metrics body\n")
    msg["From"] = "sender@example.com"
    msg["To"] = "metrics-test@example.com"
    msg["Subject"] = "metrics"
    smtp_client.send_message(msg)
    mock_api_server.wait_for_email()

    after = _metric_value(_scrape(metrics_url), 'pymta_messages_total{result="delivered"}')
    assert after >= before + 1, (before, after)


# ---------------------------------------------------------------------------
# Bearer auth on the exposition endpoint.
#
# Against a real server over real HTTP, not against the WSGI closure. Calling
# require_bearer() directly would prove the comparison works while saying
# nothing about whether start_metrics_server actually wraps the app with it,
# which is the part that would silently break. The dev stack leaves
# PYMTA_METRICS_API_KEY unset so the scrape tests above keep working, so these
# start their own server on a throwaway port.
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def authed_port():
    """One authenticated server for the whole module.

    Module-scoped on purpose: ``BaseServer.shutdown()`` waits on the
    ``serve_forever`` poll loop, which is a 0.5s interval, so a server per test
    spent about six seconds of the suite doing nothing but tearing down.
    """
    yield from _serve("s3cret")


@pytest.fixture(scope="module")
def open_port():
    yield from _serve("")


def _serve(api_key: str):
    port = _free_port()
    httpd = start_metrics_server("127.0.0.1", port, api_key)
    assert httpd is not None
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


def _raw_get(port: int, auth: bytes | None, extra: bytes = b"") -> bytes:
    """GET /metrics with a literal header, returning the status line.

    Raw bytes rather than urllib so a non-ASCII Authorization value can actually
    be put on the wire; urllib rejects it client-side and would never reach the
    server we are trying to test.
    """
    req = b"GET /metrics HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
    if auth is not None:
        req += b"Authorization: " + auth + b"\r\n"
    req += extra + b"\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(req)
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    return buf


def test_metrics_auth_accepts_the_configured_token(authed_port):
    resp = _raw_get(authed_port, b"Bearer s3cret")
    assert b"200 OK" in resp.split(b"\r\n", 1)[0], resp[:80]
    assert b"pymta_connections_total" in resp


@pytest.mark.parametrize(
    "auth",
    [
        None,  # no header at all
        b"",
        b"Bearer",
        b"Bearer wrong",
        b"Bearer s3cre",  # prefix of the real token
        b"Bearer  s3cret",  # doubled inner space, which is not stripped
        b"Basic czNjcmV0",
        b"bearer s3cret",  # scheme is case-sensitive, as on the Django side
        b"Bearer \xe9\xe9\xe9",  # not valid UTF-8; must 401, never 500
    ],
)
def test_metrics_auth_rejects_everything_else(auth, authed_port):
    resp = _raw_get(authed_port, auth)
    status = resp.split(b"\r\n", 1)[0]
    assert b"401" in status, (auth, resp[:120])
    # The exposition must not leak past the gate, whatever the status.
    assert b"pymta_connections_total" not in resp


def test_metrics_auth_ignores_surrounding_whitespace(authed_port):
    """Trailing space on the header still authenticates, and that is correct.

    Written down because it looks like a bug: wsgiref strips optional whitespace
    off every field value before WSGI sees it (RFC 9110 §5.5 allows exactly
    that), so the token compared is the bare one. No HTTP client can deliver
    that trailing space to the comparison.
    """
    resp = _raw_get(authed_port, b"Bearer s3cret  ")
    assert b"200 OK" in resp.split(b"\r\n", 1)[0], resp[:80]


def test_metrics_auth_rejects_a_second_authorization_header(authed_port):
    """Two Authorization headers must not let a good one smuggle past a bad one.

    wsgiref joins repeated headers with a comma, so the comparison sees
    ``Bearer wrong,Bearer s3cret`` and fails. Asserted rather than assumed: the
    joining behaviour is what makes it safe, and it is not ours to rely on
    silently.
    """
    resp = _raw_get(authed_port, b"Bearer wrong", extra=b"Authorization: Bearer s3cret\r\n")
    assert b"401" in resp.split(b"\r\n", 1)[0], resp[:120]
    assert b"pymta_connections_total" not in resp


def test_metrics_without_a_key_is_open(open_port):
    """Unset key means no auth. Opt-in, matching the Django side."""
    resp = _raw_get(open_port, None)
    assert b"200 OK" in resp.split(b"\r\n", 1)[0], resp[:80]
    assert b"pymta_connections_total" in resp


def test_config_limits_are_exported(metrics_url):
    """The ceilings are published so a dashboard can plot usage against them.

    Scraped rather than asserted in-process because the gauge is only populated
    from ``server.main()``: a unit test would pass with the call deleted.
    """
    text = _scrape(metrics_url)
    assert "pymta_config_limit" in text
    # Every limit the README documents as enforced should have a series.
    for name in (
        "max_incoming_email_size",
        "max_recipients_per_envelope",
        "max_envelopes_per_session",
        "max_errors_per_session",
        "max_rcpt_misses_per_session",
        "max_sessions_total",
        "max_concurrent_data",
        "max_line_length",
        "command_timeout",
        "data_timeout",
        "session_timeout",
    ):
        assert f'pymta_config_limit{{name="{name}"}}' in text, name

    # The dev stack pins nothing, so this is the shipped default and doubles as
    # a check that the gauge reports the value actually in force.
    assert (
        _metric_value(text, 'pymta_config_limit{name="max_incoming_email_size"}')
        == 10 * 1024 * 1024
    )


def test_breaker_gauge_is_exported(metrics_url):
    # Closed at rest; the point of the gauge is that "is the breaker open right
    # now" is answerable without inferring it from a counter's rate.
    text = _scrape(metrics_url)
    assert "pymta_mda_breaker_open" in text
    assert _metric_value(text, "pymta_mda_breaker_open") == 0


# ---------------------------------------------------------------------------
# The exposition port must not be a way to take down the SMTP process.
# ---------------------------------------------------------------------------


def test_slow_clients_cannot_spawn_unbounded_threads(open_port):
    """ThreadingMixIn has no ceiling and the thread starts before auth runs.

    A peer that opens connections and never completes a request would otherwise
    hold one thread each for the full handler timeout, inside the process that
    is also serving SMTP. Driven over real sockets that connect and say nothing,
    which is the whole attack.
    """
    from pymta.metrics import _MAX_SCRAPE_THREADS

    before = threading.active_count()
    hangers = []
    try:
        for _ in range(_MAX_SCRAPE_THREADS * 4):
            s = socket.create_connection(("127.0.0.1", open_port), timeout=5)
            try:
                s.sendall(b"GET /metrics HTTP/1.1\r\n")  # deliberately unfinished
            except OSError:
                # Past the cap the server closes at accept, so this write can
                # land on a reset socket. Keep only the ones it held.
                s.close()
                continue
            hangers.append(s)
        time.sleep(0.5)
        grown = threading.active_count() - before
        assert grown <= _MAX_SCRAPE_THREADS, (
            f"{len(hangers)} idle connections spawned {grown} threads, "
            f"cap is {_MAX_SCRAPE_THREADS}"
        )
    finally:
        for s in hangers:
            s.close()


def test_a_real_scrape_still_works_after_the_cap_is_hit(open_port):
    """The cap must shed load, not wedge the endpoint."""
    from pymta.metrics import _MAX_SCRAPE_THREADS

    hangers = []
    try:
        for _ in range(_MAX_SCRAPE_THREADS * 4):
            s = socket.create_connection(("127.0.0.1", open_port), timeout=5)
            try:
                s.sendall(b"GET /metrics HTTP/1.1\r\n")
            except OSError:
                # Refused past the cap; see the note in the test above.
                s.close()
                continue
            hangers.append(s)
        time.sleep(0.5)
    finally:
        for s in hangers:
            s.close()
    time.sleep(0.5)
    assert b"200 OK" in _raw_get(open_port, None)


def test_rcpt_rejected_increments_rcpt_total(metrics_url, mock_api_server, smtp_client):
    before = _metric_value(_scrape(metrics_url), 'pymta_rcpt_total{result="rejected_perm"}')

    # An RCPT that the MDA does not know about → permanent reject.
    msg = MIMEText("body\n")
    msg["From"] = "sender@example.com"
    msg["To"] = "unknown-metrics@example.com"
    msg["Subject"] = "rejected"
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        smtp_client.send_message(msg)

    after = _metric_value(_scrape(metrics_url), 'pymta_rcpt_total{result="rejected_perm"}')
    assert after >= before + 1, (before, after)

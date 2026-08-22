"""Prometheus metrics tests — pymta-only.

Skipped automatically when ``MTA_METRICS_URL`` is not set (i.e. when running
against the Postfix implementation, which has no Prometheus endpoint).
"""

import logging
import smtplib
import urllib.request
from email.mime.text import MIMEText

import pytest

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
        "command_timeout",
        "data_timeout",
        "session_timeout",
    ):
        assert f'pymta_config_limit{{name="{name}"}}' in text, name

    # The dev stack inherits MAX_INCOMING_EMAIL_SIZE=30000000 through the
    # migration fallback, so this doubles as an end-to-end check of the chain.
    assert _metric_value(text, 'pymta_config_limit{name="max_incoming_email_size"}') == 30_000_000


def test_breaker_gauge_is_exported(metrics_url):
    # Closed at rest; the point of the gauge is that "is the breaker open right
    # now" is answerable without inferring it from a counter's rate.
    text = _scrape(metrics_url)
    assert "pymta_mda_breaker_open" in text
    assert _metric_value(text, "pymta_mda_breaker_open") == 0


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

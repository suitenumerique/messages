"""Prometheus metrics for the pymta server.

The metrics HTTP endpoint is started from :mod:`pymta.server`. Each metric
intentionally has a low cardinality (no email addresses, no client IPs in
labels) to keep the time-series space bounded.
"""

import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)


_METRICS_NAMESPACE = "pymta"


CONNECTIONS_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_connections_total",
    "Total inbound TCP connections, by post-accept outcome.",
    # accepted | rejected_per_ip | rejected_per_ip_rate | rejected_global | proxy_error
    labelnames=("result",),
)

SESSIONS_ACTIVE = Gauge(
    f"{_METRICS_NAMESPACE}_sessions_active",
    "Currently active SMTP sessions (post-PROXY, pre-close).",
)

SESSIONS_PER_IP = Gauge(
    f"{_METRICS_NAMESPACE}_sessions_per_ip",
    "Distinct remote IPs currently holding at least one session.",
)

SESSION_DURATION = Histogram(
    f"{_METRICS_NAMESPACE}_session_duration_seconds",
    "Wall-clock time from accept to close of an SMTP session.",
    buckets=(0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
)

COMMANDS_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_commands_total",
    "SMTP commands processed, by verb and outcome class (2xx/4xx/5xx).",
    labelnames=("verb", "class"),
)

RCPT_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_rcpt_total",
    "RCPT TO outcomes.",
    labelnames=("result",),  # accepted | rejected_perm | rejected_temp
)

MESSAGES_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_messages_total",
    "End-of-DATA delivery outcomes.",
    labelnames=("result",),  # delivered | rejected_perm | rejected_temp
)

MESSAGE_BYTES = Histogram(
    f"{_METRICS_NAMESPACE}_message_bytes",
    "Size of received messages in bytes.",
    buckets=(1024, 10_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000),
)

MDA_REQUEST_DURATION = Histogram(
    f"{_METRICS_NAMESPACE}_mda_request_duration_seconds",
    "Latency of MDA API calls.",
    labelnames=(
        "endpoint",
        "result",
    ),  # endpoint: check|deliver, result: ok|http_5xx|timeout|error
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30),
)

DISCONNECTS_421 = Counter(
    f"{_METRICS_NAMESPACE}_disconnects_421_total",
    "Sessions where pymta replied 421 and closed the TCP connection.",
    labelnames=("reason",),  # gate_global | gate_per_ip | gate_per_ip_rate |
    # hard_error_limit | internal_error
)


SECURITY_REJECTIONS = Counter(
    f"{_METRICS_NAMESPACE}_security_rejections_total",
    "Requests rejected by an explicit hardening check, by reason.",
    labelnames=("reason",),
    # Known reasons: source_route, control_char, oversize_local, oversize_domain,
    # nul_byte, oversize_announced, max_recipients, max_envelopes, auth_offered,
    # bad_address, address_literal, bad_helo, hard_error_limit, max_rcpt_misses,
    # internal_error
)


def start_metrics_server(host: str, port: int) -> None:
    """Start the Prometheus exposition HTTP server in a daemon thread.

    ``prometheus_client.start_http_server`` already spawns a background
    thread, so this just adds a log line. Pass ``port=0`` to skip.
    """
    if port <= 0:
        logger.info("Prometheus metrics endpoint disabled (PYMTA_METRICS_PORT=0)")
        return
    start_http_server(port, addr=host)
    logger.info("Prometheus metrics endpoint listening on %s:%d/metrics", host, port)

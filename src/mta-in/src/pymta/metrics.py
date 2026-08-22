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
    # accepted | rejected_global | rejected_per_ip | rejected_per_ip_rate |
    # rejected_untrusted_proxy
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
    ),  # endpoint: check|deliver
    # result: ok | http_5xx | http_defer (non-5xx status we retry on) |
    #         http_perm (status that permanently rejects the message) |
    #         timeout | error | breaker_open
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30),
)

DISCONNECTS_421 = Counter(
    f"{_METRICS_NAMESPACE}_disconnects_421_total",
    "Sessions where pymta replied 421 and closed the TCP connection.",
    labelnames=("reason",),  # gate_global | gate_per_ip | gate_per_ip_rate |
    # max_errors_per_session | max_rcpt_misses_per_session | session_timeout |
    # internal_error
)


SECURITY_REJECTIONS = Counter(
    f"{_METRICS_NAMESPACE}_security_rejections_total",
    "Requests rejected by an explicit hardening check, by reason.",
    labelnames=("reason",),
    # Reasons that name a configurable limit use that setting's name (minus the
    # PYMTA_ prefix), so a series lines up with the CONFIG_LIMIT gauge below:
    #   max_recipients_per_envelope, max_envelopes_per_session,
    #   max_errors_per_session, max_rcpt_misses_per_session, session_timeout,
    #   data_timeout
    # The size cap is the exception, split by the phase that caught it:
    #   oversize_announced (MAIL FROM SIZE=), oversize_message (the body itself)
    # The rest are fixed checks:
    #   source_route, control_char, oversize_local, oversize_domain, nul_byte,
    #   bad_address, address_literal, bad_helo, auth_offered, untrusted_proxy,
    #   internal_error
)

MDA_BREAKER_OPEN = Gauge(
    f"{_METRICS_NAMESPACE}_mda_breaker_open",
    "1 while the MDA circuit breaker is open and calls short-circuit to 451.",
)

SESSIONS_ABANDONED = Counter(
    f"{_METRICS_NAMESPACE}_sessions_abandoned_total",
    "Sessions cut mid-flight because the SIGTERM drain deadline expired.",
)

CONFIG_LIMIT = Gauge(
    f"{_METRICS_NAMESPACE}_config_limit",
    "Configured value of each enforced limit, keyed by setting name without the "
    "PYMTA_ prefix. Lets a dashboard plot usage against the ceiling, and an alert "
    "fire on approach, without hardcoding the deployment's numbers.",
    labelnames=("name",),
)


def export_config_limits(limits: dict[str, int]) -> None:
    """Publish the configured limits as gauges. Called once at startup.

    Takes the values rather than reading ``settings`` so this module stays free
    of that import, and so the caller decides what counts as a limit worth
    watching.
    """
    for name, value in limits.items():
        CONFIG_LIMIT.labels(name=name).set(value)


def start_metrics_server(host: str, port: int) -> None:
    """Start the Prometheus exposition HTTP server in a daemon thread.

    ``prometheus_client.start_http_server`` already spawns a background
    thread, so this just adds a log line. Pass ``port=0`` to skip.
    """
    if port <= 0:
        logger.info("Prometheus metrics endpoint disabled (PYMTA_METRICS_BIND_PORT=0)")
        return
    start_http_server(port, addr=host)
    logger.info("Prometheus metrics endpoint listening on %s:%d/metrics", host, port)

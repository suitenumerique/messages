"""Prometheus metrics for the pymta server.

The metrics HTTP endpoint is started from :mod:`pymta.server`. Labels are
low-cardinality: no email address, no client IP.
"""

import logging
import socket
import socketserver
import threading
from secrets import compare_digest
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import Counter, Gauge, Histogram, make_wsgi_app

logger = logging.getLogger(__name__)


_METRICS_NAMESPACE = "pymta"


CONNECTIONS_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_connections_total",
    "Total inbound TCP connections, by post-accept outcome.",
    # accepted | rejected_blocked | rejected_drain | rejected_global |
    # rejected_per_ip | rejected_rcpt_misses | rejected_untrusted_proxy |
    # rejected_invalid_proxy | no_wire_peer
    labelnames=("result",),
)

SCRAPES_REFUSED = Counter(
    f"{_METRICS_NAMESPACE}_scrapes_refused_total",
    "Metrics HTTP connections refused because the scrape thread cap was reached.",
)

LOG_RECORDS_DROPPED = Counter(
    f"{_METRICS_NAMESPACE}_log_records_dropped_total",
    "Log lines dropped because the output stream was not read fast enough.",
)

SESSIONS_ACTIVE = Gauge(
    f"{_METRICS_NAMESPACE}_sessions_active",
    "Currently active SMTP sessions (post-PROXY, pre-close).",
)

SESSIONS_PER_IP = Gauge(
    f"{_METRICS_NAMESPACE}_sessions_per_ip",
    "Distinct remote IPs currently holding at least one session.",
)

DATA_PHASES_ACTIVE = Gauge(
    f"{_METRICS_NAMESPACE}_data_phases_active",
    "Messages currently held in memory (DATA received or being delivered). "
    "Times the size limit, this is the process's live memory exposure.",
)

SESSION_DURATION = Histogram(
    f"{_METRICS_NAMESPACE}_session_duration_seconds",
    "Wall-clock time an SMTP session held its slot, from admission to close.",
    buckets=(0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600),
)

COMMANDS_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_commands_total",
    "SMTP commands processed, by verb and outcome class (2xx/4xx/5xx).",
    # class: the last reply's class (2xx | 3xx | 4xx | 5xx), or "error" when
    # the command raised (a failed STARTTLS handshake, a connection lost
    # mid-DATA).
    labelnames=("verb", "class"),
)

REFUSALS_TOTAL = Counter(
    f"{_METRICS_NAMESPACE}_refusals_total",
    "4xx/5xx replies sent, by the verb they answered and the reply code. "
    "verb is 'none' for a line refused before any command ran.",
    labelnames=("verb", "code"),
)

IGNORED_PARAMETERS = Counter(
    f"{_METRICS_NAMESPACE}_ignored_parameters_total",
    "MAIL/RCPT parameters accepted and ignored (DSN and AUTH=), by name.",
    labelnames=("parameter",),
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
    #         timeout | error | breaker_open |
    #         pool_timeout (no free connection in our own pool)
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30),
)

DISCONNECTS_421 = Counter(
    f"{_METRICS_NAMESPACE}_disconnects_421_total",
    "Sessions where pymta replied 421 and closed the TCP connection.",
    labelnames=("reason",),  # gate_global | gate_per_ip | gate_rcpt_misses | reclaimed |
    # command_timeout | data_timeout | session_timeout | max_errors_per_session |
    # max_rcpt_misses_per_session | max_rcpt_misses_per_source |
    # max_envelopes_per_session | too_many_commands | shutdown | internal_error
)


SECURITY_REJECTIONS = Counter(
    f"{_METRICS_NAMESPACE}_security_rejections_total",
    "Requests rejected by an explicit hardening check, by reason.",
    labelnames=("reason",),
    # Reasons that name a configurable limit use that setting's name (minus the
    # PYMTA_ prefix), so a series lines up with the CONFIG_LIMIT gauge below:
    #   max_concurrent_data, max_recipients_per_envelope,
    #   max_envelopes_per_session, max_errors_per_session,
    #   max_rcpt_misses_per_session, max_rcpt_misses_per_source,
    #   command_timeout, data_timeout, session_timeout
    # The size cap is the exception, split by the phase that caught it:
    #   oversize_announced (MAIL FROM SIZE=), oversize_message (the body itself)
    # The rest are fixed checks. From pymta.address:
    #   source_route, control_char, invalid_encoding, oversize_local,
    #   oversize_domain, bad_address, address_literal, non_fqdn_sender
    # Others:
    #   nul_byte, bad_helo, untrusted_proxy, defer_all, blocked_network,
    #   blocked_sender_domain, blocked_recipient, protocol_synchronization,
    #   no_client_address, internal_error
)

TLS_CERTIFICATE_NOT_AFTER = Gauge(
    f"{_METRICS_NAMESPACE}_tls_certificate_not_after_seconds",
    "Expiry of each STARTTLS certificate as a Unix timestamp, by file. "
    "Alert on this minus time() falling under the renewal margin.",
    labelnames=("file",),
)

BARE_NEWLINE_MESSAGES = Counter(
    f"{_METRICS_NAMESPACE}_bare_newline_messages_total",
    "Messages whose body carried a bare LF or CR and was normalised to CRLF. "
    "A steady rate is sloppy mailers; a spike is worth looking at, since a bare "
    "LF mid-header is how a sender tries to add one.",
)

MDA_BREAKER_OPEN = Gauge(
    f"{_METRICS_NAMESPACE}_mda_breaker_open",
    "1 while the MDA circuit breaker is open and calls short-circuit to 451.",
)

UNDER_STRESS = Gauge(
    f"{_METRICS_NAMESPACE}_under_stress",
    "1 while four fifths of the session slots are taken: sessions waiting for "
    "a command then get the short idle timeout.",
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

    Takes the values, so this module does not import ``settings`` and the
    caller picks the limits.
    """
    for name, value in limits.items():
        CONFIG_LIMIT.labels(name=name).set(value)


def require_bearer(app, api_key: str):
    """Wrap a WSGI app so it answers 401 without ``Authorization: Bearer <key>``.

    As ``core.middlewares.PrometheusAuthMiddleware`` does in the backend, so
    both endpoints are scraped the same way.

    Compared as bytes: ``secrets.compare_digest`` raises TypeError on a str
    with non-ASCII, which would turn a garbage header into a 500. WSGI decodes
    the header as latin-1 (PEP 3333), so encoding it back loses nothing.
    """
    expected = f"Bearer {api_key}".encode()

    def wrapped(environ, start_response):
        provided = environ.get("HTTP_AUTHORIZATION", "").encode("latin-1", "replace")
        if not compare_digest(provided, expected):
            start_response("401 Unauthorized", [("Content-Type", "text/plain")])
            return [b"Unauthorized\n"]
        return app(environ, start_response)

    return wrapped


class _QuietHandler(WSGIRequestHandler):
    """Request handler that does not log to stderr and has a socket timeout.

    Without the timeout, a peer that opens connections and never sends a
    request would keep the threads ``_MetricsServer`` allows. Ten seconds is
    far more than a scrape needs.
    """

    timeout = 10

    def log_message(self, format, *args):
        pass

    def handle(self):
        # A timed-out read raises here, not in handle_one_request: closed
        # without a traceback.
        try:
            super().handle()
        except TimeoutError, socket.timeout, ConnectionError:
            self.close_connection = True


# Concurrent scrape threads. Prometheus uses one connection per scrape.
_MAX_SCRAPE_THREADS = 8


class _MetricsServer(socketserver.ThreadingMixIn, WSGIServer):
    """Exposition server with a cap on its threads.

    ``ThreadingMixIn`` has none, and a thread starts before the WSGI app
    checks the bearer token. The handler timeout bounds how long a thread
    lives, not how many there are: at N connections a second, 10N of them, in
    the process that serves SMTP. Connections past the cap are refused.
    """

    daemon_threads = True
    # Do not block process shutdown waiting on in-flight scrapes.
    block_on_close = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scrapes = 0
        self._scrape_lock = threading.Lock()

    def _end_scrape(self) -> None:
        with self._scrape_lock:
            self._scrapes -= 1

    def process_request(self, request, client_address):
        with self._scrape_lock:
            if self._scrapes >= _MAX_SCRAPE_THREADS:
                SCRAPES_REFUSED.inc()
                self.shutdown_request(request)
                return
            self._scrapes += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            # The thread never started, so nothing will run the decrement.
            self._end_scrape()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._end_scrape()


def start_metrics_server(host: str, port: int, api_key: str = "") -> WSGIServer | None:
    """Start the Prometheus exposition HTTP server in a daemon thread.

    Built on ``make_wsgi_app``: ``start_http_server`` has no hook for
    authentication.

    Returns the server, which the tests stop; production keeps it for the life
    of the process.
    """
    if port <= 0:
        logger.info("metrics_disabled")
        return None

    app = make_wsgi_app()
    if api_key:
        app = require_bearer(app, api_key)
    else:
        logger.warning(
            "metrics_unauthenticated",
            extra={
                "bind": f"{host}:{port}",
                "detail": (
                    "set PYMTA_METRICS_API_KEY or restrict the port; the exposition "
                    "leaks volumes, rejection reasons and the configured limits, and "
                    "is served from the SMTP process"
                ),
            },
        )

    # A subclass per call: ``address_family`` is a class attribute the
    # constructor reads, and set on ``_MetricsServer`` it would stay for later
    # calls (the tests start several servers).
    server_cls = type(
        "_BoundMetricsServer",
        (_MetricsServer,),
        {"address_family": socket.AF_INET6 if ":" in host else socket.AF_INET},
    )
    httpd = make_server(host, port, app, server_cls, _QuietHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    logger.info(
        "metrics_listening",
        extra={"bind": f"{host}:{port}", "auth": "bearer" if api_key else "none"},
    )
    return httpd

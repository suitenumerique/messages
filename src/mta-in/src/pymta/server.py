"""pymta entrypoint.

Run with ``python -m pymta.server``. Starts:

* the Prometheus exposition HTTP server (in a daemon thread),
* the SMTP listener (asyncio),

and exits on SIGINT/SIGTERM once the sessions in flight have finished. New
ones are answered 421 meanwhile: see ``_on_terminate``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import ssl
import time

from . import logfmt, metrics, runtime, settings
from .controller import Listener, publish_certificate_expiry, served_key_types
from .handler import InboundHandler
from .limits import IPGate
from .mda_async import MDAClient
from .smtp_protocol import close_idle_sessions, log_abandoned_sessions

# Named, not ``__name__``: run as ``python -m pymta.server``, this module is
# ``__main__``.
logger = logging.getLogger("pymta.server")

# How often the drain checks whether the last session has finished.
_DRAIN_POLL_SECONDS = 0.25

# Peak resident bytes per byte of message in flight: a second copy of the body
# while the received blocks are joined, and freed arenas not returned promptly.
_MEMORY_FACTOR = 2.2


def _configure_logging():
    """Returns what writes the lines still queued: see ``logfmt.configure``."""
    return logfmt.configure(
        settings.PYMTA_LOG_LEVEL,
        verbose_libraries=settings.PYMTA_LOG_VERBOSE_LIBRARIES,
        in_thread=True,
    )


def _check_proxy_trust_config() -> None:
    """Warn when the PROXY-protocol listener believes any peer.

    ``PYMTA_TRUSTED_PROXIES`` cannot be empty (the settings refuse to load),
    but it can name every address, for a deployment that does not know its
    balancer's addresses at boot. Network isolation is then the only
    protection: any peer that can reach the SMTP port chooses the source its
    sessions are counted and recorded under.

    Two topologies are supported: PROXY protocol on, behind a balancer, or
    off, exposed directly. Behind a balancer without it, every session would
    be attributed to the balancer's address.
    """
    if not settings.PYMTA_ENABLE_PROXY_PROTOCOL:
        return
    # A zero-prefix network (0.0.0.0/0, ::/0) matches every peer.
    catch_all = [net for net in settings.PYMTA_TRUSTED_PROXIES if net.prefixlen == 0]
    if catch_all:
        why = (
            f"PYMTA_TRUSTED_PROXIES contains {', '.join(str(n) for n in catch_all)}, "
            "which matches every peer"
        )
        logger.warning(
            "proxy_trust_unrestricted",
            extra={
                "detail": (
                    f"{why}, so any host able to reach the SMTP port directly can forge "
                    "its source IP past the per-IP caps and into Received; set "
                    "PYMTA_TRUSTED_PROXIES to the balancer's addresses"
                ),
                "port": settings.PYMTA_SMTP_BIND_PORT,
            },
        )
        return
    logger.info(
        "proxy_trust_configured",
        extra={"networks": ",".join(str(n) for n in settings.PYMTA_TRUSTED_PROXIES)},
    )


def _export_config_limits() -> None:
    """Publish the limits in force as gauges.

    A dashboard can then plot usage against the limit, and an alert rule need
    not repeat the deployment's numbers.
    """
    metrics.export_config_limits(
        {
            "max_incoming_email_size": settings.PYMTA_MAX_INCOMING_EMAIL_SIZE,
            "max_recipients_per_envelope": settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE,
            "max_envelopes_per_session": settings.PYMTA_MAX_ENVELOPES_PER_SESSION,
            "max_errors_per_session": settings.PYMTA_MAX_ERRORS_PER_SESSION,
            "max_rcpt_misses_per_session": settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION,
            "max_rcpt_misses_per_source": settings.PYMTA_MAX_RCPT_MISSES_PER_SOURCE,
            "max_sessions_total": settings.PYMTA_MAX_SESSIONS_TOTAL,
            "max_concurrent_data": settings.PYMTA_MAX_CONCURRENT_DATA,
            "command_timeout": settings.PYMTA_COMMAND_TIMEOUT,
            "data_timeout": settings.PYMTA_DATA_TIMEOUT,
            "max_line_length": settings.PYMTA_MAX_LINE_LENGTH,
            "session_timeout": settings.PYMTA_SESSION_TIMEOUT,
        }
    )


def _log_unhandled(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Log what asyncio would otherwise report on its own logger.

    That logger is filtered out of our output (logfmt.OwnRecordsOnly), so an
    exception in a timer callback, a signal handler or a task nobody awaits
    would leave no trace. The process keeps running, as it does by default.
    """
    error = context.get("exception")
    logger.error(
        "unhandled_error",
        exc_info=(type(error), error, error.__traceback__) if error else None,
        extra={"detail": str(context.get("message", ""))},
    )


def _log_certificates(ctx: ssl.SSLContext | None = None) -> None:
    """One line per STARTTLS certificate with its expiry, and the gauge.

    With the loaded context, a last line names the key types it can serve.
    """
    for cert, not_after in publish_certificate_expiry().items():
        fields: dict = {"file": cert}
        if not_after is not None:
            fields["not_after"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(not_after))
            fields["days_left"] = int((not_after - time.time()) // 86400)
        logger.info("tls_certificate", extra=fields)
    served = served_key_types(ctx) if ctx is not None else None
    if served is not None:
        logger.info(
            "tls_key_types",
            extra={
                "served": ",".join(served) or "none",
                "detail": "a sender that supports only a key type missing here fails "
                "the handshake with no shared cipher",
            },
        )


def _reload_tls(listener: Listener) -> None:
    """Read the certificate files again on SIGHUP, so a renewal needs no restart."""
    try:
        if not listener.reload_tls():
            return
    except OSError, ValueError:
        # ssl.SSLError is an OSError. The previous context stays in use.
        logger.error("tls_reload_rejected", exc_info=True, extra={"applied": False})
        return
    logger.info("tls_reloaded")
    _log_certificates(listener.tls_context)


def _on_terminate(sig: signal.Signals, stop: asyncio.Event, gate: IPGate) -> None:
    """SIGTERM or SIGINT starts the drain.

    The listener stays open: a closed one refuses the TCP connection, which a
    sender cannot tell from a network fault, so it keeps the message queued
    for this host instead of trying the next MX. A 421 moves it on (RFC 5321
    §3.1 allows one in place of the greeting; Postfix and Exim defer per host
    on it). See ``_drain_and_close``.
    """
    if runtime.is_shutting_down():
        # A second signal does not exit at once: that would cut the sessions
        # the first one let finish.
        logger.info("shutdown_already_in_progress", extra={"signal": sig.name})
        return
    runtime.request_shutdown()
    logger.info(
        "shutdown_started",
        extra={
            "signal": sig.name,
            "active_sessions": gate.total,
            "deadline_seconds": settings.PYMTA_SHUTDOWN_TIMEOUT,
        },
    )
    close_idle_sessions(gate)
    stop.set()


async def _drain_and_close(server: asyncio.AbstractServer, gate: IPGate) -> None:
    """Refuse new sessions, wait for the live ones, then stop.

    The listener stays open until the end, so arriving senders get the 421
    that sends them to another MX. Returns as soon as the last session is
    over.
    """
    started = time.monotonic()
    deadline = started + settings.PYMTA_SHUTDOWN_TIMEOUT
    while True:
        active = gate.total
        if active == 0:
            logger.info(
                "shutdown_drained", extra={"elapsed_seconds": round(time.monotonic() - started, 2)}
            )
            break
        if time.monotonic() >= deadline:
            metrics.SESSIONS_ABANDONED.inc(active)
            logger.warning(
                "shutdown_deadline_exceeded",
                extra={
                    "abandoned_sessions": active,
                    "deadline_seconds": settings.PYMTA_SHUTDOWN_TIMEOUT,
                },
            )
            log_abandoned_sessions(gate)
            break
        await asyncio.sleep(_DRAIN_POLL_SECONDS)

    server.close()
    with contextlib.suppress(TimeoutError):
        # Bounded: a transport stuck in close() must not hold the process open.
        await asyncio.wait_for(server.wait_closed(), timeout=_DRAIN_POLL_SECONDS * 10)
    logger.info("shutdown_complete")


def _check_legacy_names() -> None:
    """Warn about each Postfix-image variable still in use.

    They are read so one env file can drive both images through a switchover.
    The warning is what gets the deployment onto the pymta names afterwards.
    """
    for legacy, preferred in sorted(settings.LEGACY_IN_USE):
        logger.warning(
            "legacy_setting_in_use",
            extra={
                "legacy": legacy,
                "preferred": preferred,
                "detail": "read from the Postfix image's variable; set the pymta one instead",
            },
        )


async def _serve() -> None:
    _check_proxy_trust_config()
    _check_legacy_names()
    mda_client = MDAClient()
    try:
        await mda_client.start()
        # Computed at scrape time: the breaker's cooldown can end with no call
        # to notice it.
        metrics.MDA_BREAKER_OPEN.set_function(lambda: 1.0 if mda_client.breaker_is_open() else 0.0)
        handler = InboundHandler(mda_client)
        ip_gate = IPGate(
            # None: the gate reads each cap from settings on every acquire.
            max_total=None,
        )
        # Computed at scrape time, like the breaker gauge.
        metrics.UNDER_STRESS.set_function(lambda: 1.0 if ip_gate.under_stress() else 0.0)

        listener = Listener(handler, ip_gate=ip_gate)

        # Before the port opens: a signal's default action is to kill, and
        # asyncio would report an error on a logger we filter out.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_log_unhandled)
        loop.add_signal_handler(signal.SIGHUP, _reload_tls, listener)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_terminate, sig, stop, ip_gate)

        server = await listener.start(
            settings.PYMTA_SMTP_BIND_HOSTS, settings.PYMTA_SMTP_BIND_PORT
        )

        logger.info(
            "smtp_listening",
            extra={
                "bind": ",".join(settings.PYMTA_SMTP_BIND_HOSTS),
                "port": settings.PYMTA_SMTP_BIND_PORT,
                "hostname": settings.PYMTA_SMTP_HOSTNAME,
                "proxy_protocol": settings.PYMTA_ENABLE_PROXY_PROTOCOL,
                "size_limit": settings.PYMTA_MAX_INCOMING_EMAIL_SIZE,
                "starttls": bool(settings.PYMTA_TLS_CERT_FILE),
                "tls_min_version": settings.PYMTA_TLS_MIN_VERSION,
            },
        )
        _log_certificates(listener.tls_context)

        # Each DATA phase is buffered in RAM, so concurrent DATA phases times
        # the message size is the heap (a session that has not reached DATA
        # holds no message). ``_MEMORY_FACTOR`` times the bytes in flight is
        # what it takes.
        #
        # Logged, not enforced: the fix is to size the two settings and the
        # container together, which takes seeing the number. Without a DATA
        # cap there is no ceiling, and the line says so.
        concurrent = settings.PYMTA_MAX_CONCURRENT_DATA
        ceiling = {
            "max_concurrent_data": concurrent,
            "bounded": bool(concurrent),
            "size_limit": settings.PYMTA_MAX_INCOMING_EMAIL_SIZE,
        }
        if concurrent:
            in_flight = concurrent * settings.PYMTA_MAX_INCOMING_EMAIL_SIZE
            ceiling["in_flight_gib"] = round(in_flight / 1024**3, 1)
            ceiling["required_gib"] = round(_MEMORY_FACTOR * in_flight / 1024**3, 1)
            ceiling["detail"] = "keep the container memory limit above required_gib"
        else:
            ceiling["detail"] = (
                "concurrent DATA phases are unlimited, so heap use has no ceiling; "
                "set PYMTA_MAX_CONCURRENT_DATA to bound it"
            )
        logger.info("memory_ceiling", extra=ceiling)

        try:
            await stop.wait()
        finally:
            await _drain_and_close(server, ip_gate)
    finally:
        await mda_client.close()


def main() -> None:
    flush_log = _configure_logging()
    try:
        metrics.start_metrics_server(
            settings.PYMTA_METRICS_BIND_HOST,
            settings.PYMTA_METRICS_BIND_PORT,
            settings.PYMTA_METRICS_API_KEY,
        )
        _export_config_limits()
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    except Exception:
        # In the log's own format before the process dies with the traceback.
        logger.error("fatal_error", exc_info=True)
        raise
    finally:
        if flush_log:
            flush_log()


if __name__ == "__main__":
    main()

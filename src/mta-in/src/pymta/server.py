"""pymta entrypoint.

Run with ``python -m pymta.server``. Starts:

* the Prometheus exposition HTTP server (in a daemon thread),
* the SMTP listener (asyncio),

and exits on SIGINT/SIGTERM with an orderly shutdown that closes the listener
and waits for in-flight sessions to finish.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time

from . import logfmt, metrics, runtime, settings
from .controller import HardenedController
from .handler import InboundHandler
from .limits import IPGate
from .mda_async import MDAClient

# Named explicitly rather than __name__: this module is the entrypoint, run as
# `python -m pymta.server`, so __name__ is "__main__" and every line it emits
# would carry logger=__main__. The field is there to be filtered on.
logger = logging.getLogger("pymta.server")

# How often the drain loop checks whether the last session has finished. Short
# enough that a quiet node exits promptly, long enough not to spin.
_DRAIN_POLL_SECONDS = 0.25

# Peak resident bytes per byte of message in flight. Measured: aiosmtpd needs a
# second copy of the body while it joins the received lines, and freed arenas
# are not returned promptly.
_MEMORY_FACTOR = 2.2


def _configure_logging() -> None:
    logfmt.configure(
        settings.PYMTA_LOG_LEVEL,
        verbose_libraries=settings.PYMTA_LOG_VERBOSE_LIBRARIES,
    )


def _check_proxy_trust_config() -> None:
    """Warn loudly when the PROXY-protocol listener will believe any peer.

    Enabling PROXY protocol is normally the assertion that a balancer sits in
    front of us, which makes the balancer's address a known fact. Naming it in
    ``PYMTA_TRUSTED_PROXIES`` is strongly recommended and this warns when it is
    absent, but it does not block startup: deployments where the balancer's
    addresses are dynamic or simply unknown at boot still need to run, and
    there the network isolation has to carry the whole weight instead.

    What the allowlist buys is that the header only decides the per-IP
    rate-limit key and the ``client_address`` the MDA writes into ``Received``
    when a known balancer sent it. Without one, any peer that can open a TCP
    connection to the SMTP port decides both.

    There are two supported topologies: PROXY protocol on, behind a balancer;
    or PROXY protocol off, exposed directly. A balancer without PROXY protocol
    is not supported, because pymta would attribute every session to the
    balancer's own IP.
    """
    if not settings.PYMTA_ENABLE_PROXY_PROTOCOL:
        return
    # A zero-prefix network (0.0.0.0/0, ::/0) matches every peer, so it is the
    # empty allowlist wearing a disguise. Same posture, same warning.
    catch_all = [net for net in settings.PYMTA_TRUSTED_PROXIES if net.prefixlen == 0]
    if not settings.PYMTA_TRUSTED_PROXIES or catch_all:
        why = (
            "PYMTA_TRUSTED_PROXIES is empty"
            if not settings.PYMTA_TRUSTED_PROXIES
            else f"PYMTA_TRUSTED_PROXIES contains {', '.join(str(n) for n in catch_all)}, "
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
    """Publish the ceilings currently in force as gauges.

    Alongside the counters that report hitting them, so a dashboard plots usage
    against the limit and an alert fires on approach without the deployment's
    numbers being duplicated into the alert rule.

    Called at startup and again after an accepted SIGHUP, because four of these
    are reloadable and a gauge that still reported the startup value would
    describe a configuration no longer in force.
    """
    metrics.export_config_limits(
        {
            "max_incoming_email_size": settings.PYMTA_MAX_INCOMING_EMAIL_SIZE,
            "max_recipients_per_envelope": settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE,
            "max_envelopes_per_session": settings.PYMTA_MAX_ENVELOPES_PER_SESSION,
            "max_errors_per_session": settings.PYMTA_MAX_ERRORS_PER_SESSION,
            "max_rcpt_misses_per_session": settings.PYMTA_MAX_RCPT_MISSES_PER_SESSION,
            "max_sessions_total": settings.PYMTA_MAX_SESSIONS_TOTAL,
            "max_concurrent_data": settings.PYMTA_MAX_CONCURRENT_DATA,
            "command_timeout": settings.PYMTA_COMMAND_TIMEOUT,
            "data_timeout": settings.PYMTA_DATA_TIMEOUT,
            "max_line_length": settings.PYMTA_MAX_LINE_LENGTH,
            "session_timeout": settings.PYMTA_SESSION_TIMEOUT,
        }
    )


def _reload_settings() -> None:
    """Re-read the runtime-reloadable settings on SIGHUP.

    Only the per-connection and per-command ones; see settings._RELOADABLE for
    why the rest cannot follow. A bad value leaves the running configuration
    alone rather than taking the process down: the point of this path is to be
    usable while something is already going wrong.
    """
    try:
        changed = settings.reload_runtime_settings()
    except ValueError:
        logger.error("reload_rejected", exc_info=True, extra={"applied": False})
        return
    if changed:
        # Names only. The reloadable set includes PYMTA_BLOCKED_RECIPIENTS,
        # whose value is a list of real addresses; serialising it would put
        # recipient mail addresses in the log every time an operator adjusts a
        # blocklist. The values that are not sensitive are on the metrics
        # endpoint, republished just below.
        logger.info("reload_applied", extra={"changed": ",".join(sorted(changed))})
        _export_config_limits()
    else:
        logger.info("reload_noop")


def _on_terminate(sig: signal.Signals, stop: asyncio.Event) -> None:
    """SIGTERM/SIGINT starts the drain. One signal is the whole protocol.

    Deliberately not "close the listener and wait": a closed listener refuses
    the TCP connection, which a sender cannot tell from a network fault, so it
    keeps the message queued against this host instead of trying the next MX.
    Staying up and answering 421 is what moves traffic off this node (RFC 5321
    §3.1 allows a 421 in place of the greeting, and both Postfix and Exim treat
    it as a per-host defer). See ``_drain_and_close``.
    """
    if runtime.is_shutting_down():
        # Already draining. Impatient operators send a second TERM; honouring it
        # by exiting immediately would cut exactly the sessions the first signal
        # promised to protect.
        logger.info("shutdown_already_in_progress", extra={"signal": sig.name})
        return
    runtime.request_shutdown()
    logger.info(
        "shutdown_started",
        extra={
            "signal": sig.name,
            "active_sessions": metrics.active_sessions(),
            "deadline_seconds": settings.PYMTA_SHUTDOWN_TIMEOUT,
        },
    )
    stop.set()


async def _drain_and_close(server: asyncio.AbstractServer) -> None:
    """Refuse new sessions, wait for the live ones, then stop.

    The listener stays open throughout so arriving senders get the 421 that
    sends them to another MX. It is closed only at the very end, once nothing
    is left to serve.

    Returns as soon as the last session finishes, so a rollout costs the time
    the traffic actually needs rather than a fixed sleep.
    """
    started = time.monotonic()
    deadline = started + settings.PYMTA_SHUTDOWN_TIMEOUT
    while True:
        active = metrics.active_sessions()
        if active == 0:
            logger.info(
                "shutdown_drained", extra={"elapsed_seconds": round(time.monotonic() - started, 2)}
            )
            break
        if time.monotonic() >= deadline:
            # A negative count is active_sessions()'s "gauge unreadable"
            # sentinel, not an empty server, and inc() refuses a negative.
            known = active > 0
            if known:
                metrics.SESSIONS_ABANDONED.inc(active)
            logger.warning(
                "shutdown_deadline_exceeded",
                extra={
                    "abandoned_sessions": active if known else -1,
                    "session_count_known": known,
                    "deadline_seconds": settings.PYMTA_SHUTDOWN_TIMEOUT,
                },
            )
            break
        await asyncio.sleep(_DRAIN_POLL_SECONDS)

    server.close()
    with contextlib.suppress(TimeoutError):
        # Bounded: every session is either finished or being abandoned, but a
        # transport wedged in close() must not hold the process open forever.
        await asyncio.wait_for(server.wait_closed(), timeout=_DRAIN_POLL_SECONDS * 10)
    logger.info("shutdown_complete")


def _check_legacy_names() -> None:
    """Say which Postfix-image variables are still doing the work.

    They keep working so one env file can drive both images through a
    switchover, which is the whole point of reading them. Saying so is what
    stops the switchover from being permanent: silence would leave a
    deployment on names the recommended implementation does not own.
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
        # Computed at scrape time: the breaker can lapse with no traffic to
        # notice it, and a gauge that has to be told would stay stuck at 1.
        metrics.MDA_BREAKER_OPEN.set_function(lambda: 1.0 if mda_client.breaker_is_open() else 0.0)
        handler = InboundHandler(mda_client)
        ip_gate = IPGate(
            # All None: the gate reads each cap from settings on every acquire,
            # so SIGHUP moves them without a restart.
            max_total=None,
        )

        controller = HardenedController(
            handler,
            ip_gate=ip_gate,
            hostname=settings.PYMTA_SMTP_BIND_HOST,
            port=settings.PYMTA_SMTP_BIND_PORT,
            loop=asyncio.get_running_loop(),
        )

        # ``begin()`` is sync but only schedules; for a running loop we want to
        # await ``_create_server`` directly so the loop drives it cleanly.
        server = await controller._create_server()  # noqa: SLF001
        controller.server = server

        logger.info(
            "smtp_listening",
            extra={
                "bind": f"{settings.PYMTA_SMTP_BIND_HOST}:{settings.PYMTA_SMTP_BIND_PORT}",
                "hostname": settings.PYMTA_SMTP_HOSTNAME,
                "proxy_protocol": settings.PYMTA_ENABLE_PROXY_PROTOCOL,
                "size_limit": settings.PYMTA_MAX_INCOMING_EMAIL_SIZE,
                "starttls": bool(settings.PYMTA_TLS_CERT_FILE),
            },
        )

        # Memory is the ceiling nothing else enforces: aiosmtpd buffers each
        # DATA phase in RAM, so concurrency times message size is the heap this
        # process can be driven to. Budget about 2.2x the bytes in flight: the
        # join at end-of-DATA needs a second copy of the body, and freed arenas
        # are not returned promptly, so under concurrency that transient does
        # not stay transient.
        #
        # Logged rather than capped because the honest fix is either sizing the
        # two settings together or bounding concurrent DATA phases, and an
        # operator cannot make that call without seeing the number.
        # The DATA gate is the bound when on; a session that has not reached
        # DATA holds no message. With it off there is no finite ceiling to
        # report, so say so rather than skipping the line.
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

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGHUP, _reload_settings)
        except (NotImplementedError, AttributeError):
            pass
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_terminate, sig, stop)
            except NotImplementedError:
                # Windows / restricted environments: no signal handler support.
                pass

        try:
            await stop.wait()
        finally:
            await _drain_and_close(server)
    finally:
        await mda_client.close()


def main() -> None:
    _configure_logging()
    metrics.start_metrics_server(
        settings.PYMTA_METRICS_BIND_HOST,
        settings.PYMTA_METRICS_BIND_PORT,
        settings.PYMTA_METRICS_API_KEY,
    )
    _export_config_limits()
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

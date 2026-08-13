"""pymta entrypoint.

Run with ``python -m pymta.server``. Starts:

* the Prometheus exposition HTTP server (in a daemon thread),
* the SMTP listener (asyncio),

and exits on SIGINT/SIGTERM with an orderly shutdown that closes the listener
and waits for in-flight sessions to finish.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import metrics, settings
from .controller import HardenedController
from .handler import InboundHandler
from .limits import IPGate
from .mda_async import MDAClient

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.PYMTA_LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
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
            "SECURITY: PROXY protocol is enabled but %s, so a PROXY header is trusted "
            "from any peer. Any host able to reach port %s directly can forge its "
            "source IP past the per-IP caps and into the Received header. Set it to "
            "the load balancer's IPs/CIDRs, and make sure the port is reachable only "
            "from the balancer.",
            why,
            settings.PYMTA_SMTP_PORT,
        )
        return
    logger.info(
        "PROXY protocol enabled; trusting headers only from %s",
        ", ".join(str(net) for net in settings.PYMTA_TRUSTED_PROXIES),
    )


async def _serve() -> None:
    _check_proxy_trust_config()
    mda_client = MDAClient()
    try:
        await mda_client.start()
        handler = InboundHandler(mda_client)
        ip_gate = IPGate(
            max_total=settings.PYMTA_MAX_SESSIONS_TOTAL,
            max_per_ip=settings.PYMTA_MAX_SESSIONS_PER_IP,
            max_per_ip_per_minute=settings.PYMTA_MAX_SESSIONS_PER_IP_PER_MINUTE,
        )

        controller = HardenedController(
            handler,
            ip_gate=ip_gate,
            hostname=settings.PYMTA_SMTP_HOST,
            port=settings.PYMTA_SMTP_PORT,
            loop=asyncio.get_running_loop(),
        )

        # ``begin()`` is sync but only schedules; for a running loop we want to
        # await ``_create_server`` directly so the loop drives it cleanly.
        server = await controller._create_server()  # noqa: SLF001
        controller.server = server

        logger.info(
            "pymta SMTP listening on %s:%d (hostname=%s, proxy_protocol=%s, size=%d)",
            settings.PYMTA_SMTP_HOST,
            settings.PYMTA_SMTP_PORT,
            settings.PYMTA_HOSTNAME,
            settings.PYMTA_ENABLE_PROXY_PROTOCOL,
            settings.MAX_INCOMING_EMAIL_SIZE,
        )

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                # Windows / restricted environments: no signal handler support.
                pass

        try:
            await stop.wait()
        finally:
            logger.info("shutting down pymta SMTP listener")
            server.close()
            try:
                await asyncio.wait_for(
                    server.wait_closed(), timeout=settings.PYMTA_SHUTDOWN_TIMEOUT
                )
            except TimeoutError:
                logger.warning(
                    "graceful shutdown deadline (%ds) exceeded; in-flight sessions abandoned",
                    settings.PYMTA_SHUTDOWN_TIMEOUT,
                )
    finally:
        await mda_client.close()


def main() -> None:
    _configure_logging()
    metrics.start_metrics_server(settings.PYMTA_METRICS_HOST, settings.PYMTA_METRICS_PORT)
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Unified entrypoint for the client-bridge service.

Starts IMAP and/or SMTP servers based on settings
in a single asyncio event loop.
"""

import asyncio
import logging
import sys
from argparse import Namespace
from contextlib import AsyncExitStack

from src import settings

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _start_imap(stack: AsyncExitStack, api_url: str, api_secret: str, host: str, port: int):
    """Start the pymap IMAP server."""
    from pymap.backend import backends
    from pymap.service import services

    from src.backend import MessagesBackend

    backends.add("messages-api", MessagesBackend)

    args = Namespace(
        host=host,
        port=port,
        debug=True,
        cert=None,
        key=None,
        tls=False,
        passlib_cfg=None,
        proxy_protocol=None,
        inherited_sockets=None,
        api_url=api_url,
        api_secret=api_secret,
        backend="messages-api",
    )

    backend, config = await MessagesBackend.init(args)
    config.apply_context()

    service_types = list(services.values())
    svc_instances = [svc_type(backend, config) for svc_type in service_types]

    await backend.start(stack)
    for service in svc_instances:
        await service.start(stack)

    logger.info("IMAP server started on %s:%d", host, port)


async def _start_smtp(stack: AsyncExitStack, api_url: str, api_secret: str, host: str, port: int):
    """Start the aiosmtpd SMTP submission server."""
    from aiosmtpd.controller import Controller

    from src.api.client import MessagesAPIClient
    from src.submission import SubmissionAuthenticator, SubmissionHandler

    api_client = MessagesAPIClient(api_url, api_secret=api_secret)
    handler = SubmissionHandler(api_client)
    authenticator = SubmissionAuthenticator(api_client)

    controller = Controller(
        handler,
        hostname=host,
        port=port,
        authenticator=authenticator,
        auth_require_tls=False,
        auth_required=True,
        timeout=settings.SMTP_SESSION_TIMEOUT,
        command_call_limit={"DATA": settings.SMTP_MAX_MESSAGES_PER_SESSION},
    )
    controller.start()
    stack.callback(controller.stop)

    logger.info("SMTP submission server started on %s:%d", host, port)


async def main():
    """Main entrypoint: start IMAP and/or SMTP based on settings."""
    if not settings.ENABLE_IMAP and not settings.ENABLE_SMTP:
        logger.error("Both IMAP and SMTP bridges are disabled. Nothing to do.")
        sys.exit(1)

    async with AsyncExitStack() as stack:
        if settings.ENABLE_IMAP:
            await _start_imap(
                stack,
                settings.MESSAGES_API_BASE_URL,
                settings.CLIENTBRIDGE_API_SECRET,
                settings.IMAP_HOST,
                settings.IMAP_PORT,
            )
        else:
            logger.info("IMAP bridge disabled")

        if settings.ENABLE_SMTP:
            await _start_smtp(
                stack,
                settings.MESSAGES_API_BASE_URL,
                settings.CLIENTBRIDGE_API_SECRET,
                settings.SMTP_HOST,
                settings.SMTP_PORT,
            )
        else:
            logger.info("SMTP bridge disabled")

        # Run forever
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info("Shutting down client-bridge...")


if __name__ == "__main__":
    asyncio.run(main())

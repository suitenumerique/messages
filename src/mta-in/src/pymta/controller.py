"""aiosmtpd Controller wired to our :class:`HardenedSMTP` factory.

The Controller itself is unchanged structurally — all hardening lives inside
:class:`HardenedSMTP` so the admission gate runs in the same coroutine that
will dispatch SMTP verbs.
"""

from __future__ import annotations

import asyncio
import logging
import ssl

from aiosmtpd.controller import UnthreadedController

from . import settings
from .handler import InboundHandler
from .limits import IPGate
from .smtp_protocol import HardenedSMTP

logger = logging.getLogger(__name__)


def build_smtp_kwargs(*, tls_context: ssl.SSLContext | None) -> dict:
    """Centralise the SMTP-class options driven by settings."""
    return {
        "hostname": settings.PYMTA_HOSTNAME,
        "ident": settings.PYMTA_IDENT,
        "data_size_limit": settings.MAX_INCOMING_EMAIL_SIZE,
        "enable_SMTPUTF8": settings.PYMTA_ENABLE_SMTPUTF8,
        "timeout": settings.PYMTA_COMMAND_TIMEOUT,
        # Per-verb call ceilings (defence against pipelining floods). Numbers
        # come from "what a sane sender would ever do in one TCP session";
        # anything above means the peer is hammering us.
        "command_call_limit": {
            "EHLO": 4,
            "HELO": 4,
            "NOOP": 5,
            "MAIL": settings.PYMTA_MAX_ENVELOPES_PER_CONNECTION + 2,
            "RCPT": settings.PYMTA_MAX_RECIPIENTS * settings.PYMTA_MAX_ENVELOPES_PER_CONNECTION
            + 10,
            "DATA": settings.PYMTA_MAX_ENVELOPES_PER_CONNECTION + 2,
            "RSET": 20,
            "QUIT": 1,
            # STARTTLS pinned explicitly so a future contributor cannot raise
            # the "*" bucket and silently let a peer burn TLS handshakes by
            # repeating EHLO/STARTTLS within one TCP session.
            "STARTTLS": 2,
            "*": 25,
        },
        "proxy_protocol_timeout": (
            settings.PYMTA_PROXY_PROTOCOL_TIMEOUT if settings.PYMTA_ENABLE_PROXY_PROTOCOL else None
        ),
        "tls_context": tls_context,
        # Plaintext AUTH on port 25 is unsafe; AUTH stays off entirely.
        "auth_require_tls": True,
        "auth_required": False,
        "auth_exclude_mechanism": ("LOGIN", "PLAIN"),  # ggignore
    }


def load_tls_context() -> ssl.SSLContext | None:
    """Build a TLS context from the configured cert/key, or None.

    Returning None disables STARTTLS — aiosmtpd will not advertise it.
    """
    cert = settings.PYMTA_TLS_CERT_FILE
    key = settings.PYMTA_TLS_KEY_FILE
    if not cert or not key:
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    # As an MTA we accept any client identity; we just want our side encrypted.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(cert, key)
    return ctx


class HardenedController(UnthreadedController):
    """:class:`UnthreadedController` returning a :class:`HardenedSMTP`."""

    def __init__(
        self,
        handler: InboundHandler,
        *,
        ip_gate: IPGate,
        hostname: str,
        port: int,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self._ip_gate = ip_gate
        tls_context = load_tls_context()
        self._smtp_kwargs = build_smtp_kwargs(tls_context=tls_context)
        super().__init__(handler, hostname=hostname, port=port, loop=loop)

    def factory(self) -> HardenedSMTP:  # type: ignore[override]
        return HardenedSMTP(self.handler, ip_gate=self._ip_gate, **self._smtp_kwargs)

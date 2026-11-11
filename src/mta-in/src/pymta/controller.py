"""The SMTP listener: its options, read from settings, and its TLS context."""

from __future__ import annotations

import asyncio
import logging
import ssl
import warnings

from . import metrics, settings
from .handler import InboundHandler
from .limits import IPGate
from .smtp_protocol import SMTPConnection

logger = logging.getLogger(__name__)

_TLS_MINIMUM = {
    "1.0": ssl.TLSVersion.TLSv1,
    "1.1": ssl.TLSVersion.TLSv1_1,
    "1.2": ssl.TLSVersion.TLSv1_2,
    "1.3": ssl.TLSVersion.TLSv1_3,
}


def build_smtp_kwargs(*, tls_context: ssl.SSLContext | None) -> dict:
    """The :class:`SMTPConnection` options that come from settings."""
    return {
        "hostname": settings.PYMTA_SMTP_HOSTNAME,
        "ident": settings.PYMTA_SMTP_IDENT,
        "data_size_limit": settings.PYMTA_MAX_INCOMING_EMAIL_SIZE,
        "enable_smtputf8": settings.PYMTA_ENABLE_SMTPUTF8,
        "timeout": settings.PYMTA_COMMAND_TIMEOUT,
        # Off unless PYMTA_REQUIRE_TLS: on a public MX, refusing a sender that
        # does not do STARTTLS loses mail rather than protecting it.
        "require_starttls": settings.PYMTA_REQUIRE_TLS and tls_context is not None,
        # Times each command may be sent in one session. Numbers come from
        # "what a sane sender would ever do in one TCP session"; anything
        # above means the peer is hammering us.
        "command_call_limit": {
            "EHLO": 4,
            "HELO": 4,
            "NOOP": 5,
            "MAIL": settings.PYMTA_MAX_ENVELOPES_PER_SESSION + 2,
            "RCPT": settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE
            * settings.PYMTA_MAX_ENVELOPES_PER_SESSION
            + 10,
            "DATA": settings.PYMTA_MAX_ENVELOPES_PER_SESSION + 2,
            "RSET": 20,
            "QUIT": 1,
            # Pinned so that raising the "*" bucket cannot let a peer burn TLS
            # handshakes by repeating STARTTLS within one TCP session.
            "STARTTLS": 2,
            "*": 25,
        },
        "proxy_protocol_timeout": (
            settings.PYMTA_PROXY_PROTOCOL_TIMEOUT if settings.PYMTA_ENABLE_PROXY_PROTOCOL else None
        ),
        "tls_context": tls_context,
    }


def load_tls_context() -> ssl.SSLContext | None:
    """Build a TLS context from the configured cert/key pairs, or None.

    None means STARTTLS is neither offered nor accepted.

    Loading more than one pair is how dual certificates work: OpenSSL holds a
    slot per key type and, at each handshake, presents the certificate the
    client said it can verify. Two pairs of the *same* key type is not an
    error — the later simply takes the slot — so the second of two RSA certs
    is the one served.
    """
    pairs = settings.PYMTA_TLS_CERT_PAIRS
    if not pairs:
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    # As an MTA we accept any client identity; we just want our side encrypted.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    minimum = _TLS_MINIMUM[settings.PYMTA_TLS_MIN_VERSION]
    if minimum < ssl.TLSVersion.TLSv1_2:
        # OpenSSL 3 refuses the SHA-1 handshake signatures of TLS 1.0 and 1.1
        # above security level 0.
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    with warnings.catch_warnings():
        # Python warns that 1.0 and 1.1 are deprecated; here they were asked for.
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.minimum_version = minimum
    for cert, key in pairs:
        ctx.load_cert_chain(cert, key)
    return ctx


def certificate_not_after(path: str) -> float | None:
    """Expiry of the first certificate in ``path`` as a Unix timestamp, or None.

    The stdlib has no public call that reads a certificate file, and one gauge
    does not justify adding ``cryptography`` to the process facing port 25.
    This is the helper the ``ssl`` test-suite uses; if it goes away the gauge
    goes with it and nothing else does.
    """
    try:
        decoded = ssl._ssl._test_decode_cert(path)  # noqa: SLF001
        return float(ssl.cert_time_to_seconds(decoded["notAfter"]))
    except Exception:  # noqa: BLE001
        return None


def publish_certificate_expiry() -> dict[str, float | None]:
    """Export each certificate's expiry as a gauge. Returns path -> timestamp."""
    expiries = {cert: certificate_not_after(cert) for cert, _ in settings.PYMTA_TLS_CERT_PAIRS}
    for cert, not_after in expiries.items():
        if not_after is not None:
            metrics.TLS_CERTIFICATE_NOT_AFTER.labels(file=cert).set(not_after)
    return expiries


class Listener:
    """Builds one :class:`SMTPConnection` per accepted connection."""

    def __init__(self, handler: InboundHandler, *, ip_gate: IPGate):
        self.handler = handler
        self._ip_gate = ip_gate
        self._smtp_kwargs = build_smtp_kwargs(tls_context=load_tls_context())

    def reload_tls(self) -> bool:
        """Read the certificate files again. Returns False when STARTTLS is off.

        Connections accepted from now on get the new context; a handshake
        already done keeps the certificate it was made with. Raises, leaving
        the current context in place, if the files do not load.
        """
        if not settings.PYMTA_TLS_CERT_PAIRS:
            return False
        self._smtp_kwargs["tls_context"] = load_tls_context()
        return True

    def factory(self) -> SMTPConnection:
        return SMTPConnection(self.handler, ip_gate=self._ip_gate, **self._smtp_kwargs)

    async def start(self, host: str, port: int) -> asyncio.Server:
        return await asyncio.get_running_loop().create_server(self.factory, host=host, port=port)

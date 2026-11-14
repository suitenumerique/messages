"""The SMTP listener: its options, read from settings, and its TLS context."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import warnings

from . import metrics, pem_bundle, settings
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


# Key exchanges of TLS 1.2 and below that a handshake can complete with, as
# ``SSLContext.get_ciphers`` names them: ECDHE, and static RSA.
_SERVED_KEY_EXCHANGES = ("kx-ecdhe", "kx-rsa")


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
        # Times each command may be sent in one session, above what a
        # legitimate sender does.
        "command_call_limit": {
            "EHLO": 4,
            "HELO": 4,
            "NOOP": 20,
            "MAIL": settings.PYMTA_MAX_ENVELOPES_PER_SESSION + 2,
            "RCPT": settings.PYMTA_MAX_RECIPIENTS_PER_ENVELOPE
            * settings.PYMTA_MAX_ENVELOPES_PER_SESSION
            + 10,
            "DATA": settings.PYMTA_MAX_ENVELOPES_PER_SESSION + 2,
            # NOOP, RSET, VRFY and HELP are counted between two accepted
            # messages: see SMTPConnection._forgive_junk.
            "RSET": 20,
            "QUIT": 1,
            # Its own entry, so raising "*" cannot let a peer repeat TLS
            # handshakes in one session.
            "STARTTLS": 2,
            "*": 25,
        },
        "proxy_protocol_timeout": (
            settings.PYMTA_PROXY_PROTOCOL_TIMEOUT if settings.PYMTA_ENABLE_PROXY_PROTOCOL else None
        ),
        "tls_context": tls_context,
    }


def _bundle_groups(cert: str, key: str) -> list[str] | None:
    """The key + chain groups when ``cert`` and ``key`` are one multi-key file."""
    if os.path.realpath(cert) != os.path.realpath(key):
        return None
    return pem_bundle.split_file(cert)


def load_tls_context() -> ssl.SSLContext | None:
    """Build a TLS context from the configured cert/key pairs, or None.

    None means STARTTLS is neither offered nor accepted.

    Several pairs give dual certificates: OpenSSL holds one per key type and
    presents, at each handshake, the one the client said it can verify. Of two
    pairs with the same key type, the later is the one served. One PEM file
    given as both cert and key may carry several key + chain groups, and each
    counts as a pair.
    """
    pairs = settings.PYMTA_TLS_CERT_PAIRS
    if not pairs:
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    # No client certificate is asked for.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # No session tickets. OpenSSL encrypts them with a key it keeps for as
    # long as the context lives, here until the next SIGHUP or restart. With
    # that key, a recorded TLS 1.2 session whose ticket was captured can be
    # decrypted. A sender pays one full handshake per connection instead.
    ctx.options |= ssl.OP_NO_TICKET
    ctx.num_tickets = 0
    minimum = _TLS_MINIMUM[settings.PYMTA_TLS_MIN_VERSION]
    ciphers = settings.PYMTA_TLS_CIPHERS
    if minimum < ssl.TLSVersion.TLSv1_2:
        # OpenSSL 3 refuses the SHA-1 handshake signatures of TLS 1.0 and 1.1
        # above security level 0.
        ctx.set_ciphers(f"{ciphers or 'DEFAULT'}:@SECLEVEL=0")
    elif ciphers:
        ctx.set_ciphers(ciphers)
    if not ciphers:
        # Both default lists name suites that can never be negotiated here:
        # DHE (no DH parameters are loaded), PSK and SRP (no such credentials).
        # Kept to the two key exchanges this server does, so that the list is
        # what it serves. TLS 1.3 suites are not part of this list. A list the
        # operator gave is left as written.
        ctx.set_ciphers(
            ":".join(
                suite["name"]
                for suite in ctx.get_ciphers()
                if suite["kea"] in _SERVED_KEY_EXCHANGES
            )
        )
    with warnings.catch_warnings():
        # Python warns that 1.0 and 1.1 are deprecated; here they were asked for.
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.minimum_version = minimum
    for cert, key in pairs:
        groups = _bundle_groups(cert, key)
        if groups is None:
            ctx.load_cert_chain(cert, key)
            continue
        for group in groups:
            with pem_bundle.readable_path(group) as path:
                ctx.load_cert_chain(path)
    return ctx


_KEY_TYPES = ("ecdsa", "rsa")


def _handshake_succeeds(server_ctx: ssl.SSLContext, ciphers: str) -> bool:
    """Whether a TLS 1.2 client limited to ``ciphers`` completes a handshake."""
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    client_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    client_ctx.set_ciphers(ciphers)
    to_server, to_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_ctx.wrap_bio(to_client, to_server)
    server = server_ctx.wrap_bio(to_server, to_client, server_side=True)
    done = {"client": False, "server": False}
    for _ in range(8):
        for side, peer in (("client", client), ("server", server)):
            if done[side]:
                continue
            try:
                peer.do_handshake()
                done[side] = True
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLError:
                return False
        if all(done.values()):
            return True
    return False


def served_key_types(ctx: ssl.SSLContext) -> list[str] | None:
    """The key types ``ctx`` can complete a handshake with, by trying each.

    Tells an operator without shell access which certificates the process
    holds. Each key type is tried with every suite the context allows for it,
    so a ``PYMTA_TLS_CIPHERS`` that leaves out one suite does not read as a
    missing certificate. None when the context only speaks TLS 1.3,
    whose suites do not depend on the key type.
    """
    if ctx.minimum_version > ssl.TLSVersion.TLSv1_2:
        return None
    served = []
    for key_type in _KEY_TYPES:
        # By key type only: a suite's "protocol" is the first version that
        # defines it (ECDHE-ECDSA-AES256-SHA says TLSv1.0), and it still runs
        # in TLS 1.2. The client is held to 1.2, so the handshake decides.
        suites = [
            suite["name"] for suite in ctx.get_ciphers() if suite["auth"] == f"auth-{key_type}"
        ]
        if suites and _handshake_succeeds(ctx, ":".join(suites)):
            served.append(key_type)
    return served


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
    expiries: dict[str, float | None] = {}
    for cert, key in settings.PYMTA_TLS_CERT_PAIRS:
        try:
            groups = _bundle_groups(cert, key)
        except OSError, ValueError:
            # The load fails with the same message.
            groups = None
        if groups is None:
            expiries[cert] = certificate_not_after(cert)
            continue
        for index, group in enumerate(groups, 1):
            with pem_bundle.readable_path(group) as path:
                expiries[f"{cert}[{index}]"] = certificate_not_after(path)
    # A file that became a bundle (or the reverse) changes its labels; the old
    # series would otherwise stay at its last value and keep an alert firing.
    metrics.TLS_CERTIFICATE_NOT_AFTER.clear()
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

    @property
    def tls_context(self) -> ssl.SSLContext | None:
        return self._smtp_kwargs["tls_context"]

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

    async def start(self, hosts: tuple[str, ...], port: int) -> asyncio.Server:
        """Listen on each of ``hosts``. One socket per address family and host."""
        return await asyncio.get_running_loop().create_server(
            self.factory, host=list(hosts), port=port
        )

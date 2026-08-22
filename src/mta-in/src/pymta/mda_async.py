"""Async HTTP client for the MDA inbound API.

The Postfix milter uses ``requests`` (sync, see ``src/api/mda.py``). pymta
runs inside an asyncio event loop, so blocking HTTP calls would freeze the
whole SMTP server; we mirror the same JWT contract here on top of httpx.

The MDA contract, kept identical to the milter so both implementations stay
swap-compatible, is:

* ``POST /inbound/mta/check/`` with ``application/json`` body
  ``{"addresses": [...]}`` → returns ``{addr: bool}``.
* ``POST /inbound/mta/deliver/`` with ``message/rfc822`` body (the full
  message bytes). The metadata (sender, recipients, client info, size) is
  carried as JWT claims, not in the body.

Every request is signed with a short-lived HS256 JWT whose body_hash claim
binds the JWT to the exact bytes being posted (replay-proofing).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import jwt

from . import metrics, settings

logger = logging.getLogger(__name__)

# Local development URLs are the only place we tolerate a plaintext MDA;
# anywhere else a leaked JWT secret on the wire is a credential incident.
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
# Below this many bytes a shared HS256 secret is brute-forceable; refuse to
# even start the process rather than minting weak tokens.
_MIN_SECRET_LENGTH = 32

# HTTP statuses on which a message is rejected *permanently* (SMTP 5xx). The
# list is an allow-list, not a "4xx means permanent" rule, because the default
# has to be the safe one: deferring costs a retry, bouncing loses the mail.
#
#   400: the MDA could not parse the message, or the request was malformed.
#   413: over the MDA's own size limit. Retrying sends the same oversized bytes.
#   415: wrong Content-Type. Ours to fix, but no retry will change it.
#
# Everything else defers:
#   207: Multi-Status. *Some* recipients were delivered, some were not. The
#         MDA has no per-recipient reply channel back to us, so the only way
#         the failed ones ever land is if the sending MTA retries the whole
#         envelope. That re-delivers to the recipients who already succeeded,
#         which is the correct trade against silently losing the rest.
#   401 / 403: secret rotation skew, or an `exp` the MDA's clock reads as
#         past. A routine operational event must not bounce real mail.
#   404: a routing/deployment mistake, not a verdict on this message.
#   429: throttling. Retrying later is the intended response.
#
# Delivery only. Each entry is a verdict on the *message*, and a recipient
# check carries no message: a 400/413/415 there is a fault in the check request
# we built, so it defers like any other unexpected status rather than telling
# the sender the mailbox is permanently bad.
_PERMANENT_STATUSES = frozenset({400, 413, 415})


@dataclass(frozen=True)
class MDAResult:
    """Result of an MDA call.

    ``ok`` is true iff the call returned HTTP 200 with a JSON body that the
    caller can rely on. ``temp_fail`` distinguishes "try again later" (network
    error, timeout, 5xx, and every status the endpoint does not name as
    permanent) from a permanent rejection; only ``deliver`` names any, via
    :data:`_PERMANENT_STATUSES`. ``payload`` is the decoded JSON body when
    available.

    ``payload`` is always a dict, so callers can ``.get()`` without guarding.
    A body that is absent, unparseable, or a JSON value that is not an object
    (a list, a string, ``null``) becomes ``{}``. An empty payload therefore
    means "no usable answer", never a verdict: callers must not read a missing
    key as a negative one.
    """

    ok: bool
    temp_fail: bool
    payload: dict
    status_code: int


class MDAClient:
    """Thin async wrapper over the MDA REST API.

    Lifetime: one instance per server process. Reuses one
    :class:`httpx.AsyncClient` so the HTTP channel survives many SMTP
    sessions (HTTP keep-alive); each individual SMTP transaction still
    blocks on a synchronous MDA call so there is no on-disk queue.
    """

    def __init__(  # noqa: PLR0913
        self,
        base_url: str | None = None,
        secret: str | None = None,
        timeout: int | None = None,
        breaker_threshold: int | None = None,
        breaker_cooldown: int | None = None,
        jwt_ttl: int | None = None,
        clock=time.monotonic,
    ):
        self.base_url = (base_url or settings.MDA_API_BASE_URL).rstrip("/") + "/"
        self.secret = secret or settings.MDA_API_SECRET
        self.timeout = timeout if timeout is not None else settings.MDA_API_TIMEOUT
        self.jwt_ttl = jwt_ttl if jwt_ttl is not None else settings.MDA_API_JWT_TTL
        self._breaker_threshold = (
            breaker_threshold if breaker_threshold is not None else settings.MDA_BREAKER_THRESHOLD
        )
        self._breaker_cooldown = (
            breaker_cooldown if breaker_cooldown is not None else settings.MDA_BREAKER_COOLDOWN
        )
        self._clock = clock
        # Counts consecutive failures. Reset to 0 by any successful call.
        self._consecutive_failures = 0
        # Monotonic-time deadline until which the breaker stays open. None
        # when closed; a future timestamp when open.
        self._open_until: float | None = None
        self._client: httpx.AsyncClient | None = None
        self._validate_credentials()

    def _validate_credentials(self) -> None:
        """Warn loudly at startup about a weak secret or a plaintext non-local MDA URL.

        Warnings, not hard failures: the shared dev secret
        ``my-shared-secret-mda`` (20 chars) is intentionally short and dev
        deployments talk to the MDA over the docker bridge without TLS, so
        refusing to start would block the normal local workflow. See the
        production checklist in the README for what these should look like.
        """
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and host not in _LOCAL_HOSTNAMES:
            logger.warning(
                "MDA_API_BASE_URL uses plaintext http:// for non-local host %r "
                "(%r). The JWT bearer token will traverse the network in clear. "
                "Configure https:// in production.",
                host,
                self.base_url,
            )
        if not self.secret:
            logger.warning(
                "MDA_API_SECRET is empty; every MDA call will fail to sign and "
                "all mail will be deferred. Configure the shared secret."
            )
        elif len(self.secret) < _MIN_SECRET_LENGTH:
            logger.warning(
                "MDA_API_SECRET is %d bytes; recommended minimum is %d. "
                "Short HS256 secrets are brute-forceable from a captured JWT.",
                len(self.secret),
                _MIN_SECRET_LENGTH,
            )

    async def start(self) -> httpx.AsyncClient:
        """Open the persistent HTTP client. Idempotent."""
        if self._client is None:
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            self._client = httpx.AsyncClient(timeout=self.timeout, limits=limits)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_jwt(self, body: bytes, metadata: dict) -> str:
        if not self.secret:
            raise RuntimeError("MDA_API_SECRET is required to sign MDA API requests")
        # Spread metadata FIRST so a stray metadata key named "exp" or
        # "body_hash" cannot shadow the security-relevant claims.
        claims = {
            **metadata,
            "exp": datetime.datetime.now(tz=datetime.UTC)
            + datetime.timedelta(seconds=self.jwt_ttl),
            "body_hash": hashlib.sha256(body).hexdigest(),
        }
        return jwt.encode(claims, self.secret, algorithm="HS256")

    def _breaker_open(self) -> bool:
        """True when the circuit is currently shedding traffic."""
        if self._open_until is None:
            return False
        if self._clock() >= self._open_until:
            # Cool-down elapsed; let the next request probe upstream.
            self._open_until = None
            self._consecutive_failures = 0
            metrics.MDA_BREAKER_OPEN.set(0)
            return False
        return True

    def _record_failure(self) -> None:
        if not self._breaker_threshold:
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold and self._open_until is None:
            self._open_until = self._clock() + self._breaker_cooldown
            metrics.MDA_BREAKER_OPEN.set(1)
            logger.warning(
                "MDA circuit breaker OPEN after %d consecutive failures; fast-failing for %ds",
                self._consecutive_failures,
                self._breaker_cooldown,
            )

    def _record_success(self) -> None:
        if self._consecutive_failures and self._open_until is None:
            logger.info("MDA recovered after %d consecutive failures", self._consecutive_failures)
        self._consecutive_failures = 0

    async def _post(  # noqa: PLR0913
        self,
        path: str,
        content_type: str,
        body: bytes,
        metadata: dict,
        endpoint_label: str,
        permanent_statuses: frozenset[int] = frozenset(),
    ) -> MDAResult:
        if self._breaker_open():
            metrics.MDA_REQUEST_DURATION.labels(
                endpoint=endpoint_label, result="breaker_open"
            ).observe(0)
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=0)

        client = self._client or await self.start()

        url = self.base_url + path.lstrip("/")
        token = self._build_jwt(body, metadata)
        headers = {"Content-Type": content_type, "Authorization": f"Bearer {token}"}

        start = self._clock()
        try:
            response = await client.post(url, content=body, headers=headers)
        except httpx.TimeoutException:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="timeout").observe(
                self._clock() - start
            )
            logger.warning("MDA %s timeout after %.2fs", endpoint_label, self._clock() - start)
            self._record_failure()
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=0)
        except httpx.HTTPError:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="error").observe(
                self._clock() - start
            )
            logger.exception("MDA %s transport error", endpoint_label)
            self._record_failure()
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=0)

        elapsed = self._clock() - start

        # JSON decode is best-effort; some error bodies may be HTML. Anything
        # that is not a JSON object collapses to {} so callers get a total
        # ``.get()`` instead of an AttributeError on a list or None body.
        try:
            payload = response.json() if response.content else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            logger.warning(
                "MDA %s returned HTTP %d with a non-object JSON body (%s)",
                endpoint_label,
                response.status_code,
                type(payload).__name__,
            )
            payload = {}

        status = response.status_code
        if status == 200:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="ok").observe(
                elapsed
            )
            self._record_success()
            return MDAResult(ok=True, temp_fail=False, payload=payload, status_code=status)

        # Two independent judgements here, kept separate:
        #
        #  * temp_fail: what we tell the *sender*. Permanent only for the
        #    statuses that say "this message is bad"; everything else defers.
        #  * the circuit breaker: a *liveness* signal. Only 5xx (and the
        #    transport failures above) indicate the MDA is unhealthy. A 207 or
        #    a 401 is a complete answer from a healthy MDA, so it closes the
        #    breaker rather than opening it.
        temp = status not in permanent_statuses
        unhealthy = status >= 500
        if unhealthy:
            result_label = "http_5xx"
        else:
            result_label = "http_defer" if temp else "http_perm"
        metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result=result_label).observe(
            elapsed
        )
        logger.warning(
            "MDA %s returned HTTP %d (%s)",
            endpoint_label,
            status,
            "deferring" if temp else "rejecting permanently",
        )
        if unhealthy:
            self._record_failure()
        else:
            self._record_success()
        return MDAResult(ok=False, temp_fail=temp, payload=payload, status_code=status)

    async def check_recipient(self, address: str) -> MDAResult:
        """Ask the MDA whether a single recipient mailbox exists."""
        body = json.dumps({"addresses": [address]}, separators=(",", ":")).encode("utf-8")
        return await self._post(
            "inbound/mta/check/",
            "application/json",
            body,
            metadata={},
            endpoint_label="check",
        )

    async def deliver(  # noqa: PLR0913
        self,
        *,
        message: bytes,
        sender: str,
        original_recipients: list[str],
        client_address: str | None,
        client_port: str | None,
        client_hostname: str | None,
        client_helo: str | None,
    ) -> MDAResult:
        """Push the complete message to the MDA for synchronous delivery."""
        metadata = {
            "sender": sender,
            "original_recipients": list(original_recipients),
            "client_address": client_address,
            "client_port": client_port,
            "client_hostname": client_hostname,
            "client_helo": client_helo,
            "size": str(len(message)),
        }
        return await self._post(
            "inbound/mta/deliver/",
            "message/rfc822",
            message,
            metadata=metadata,
            endpoint_label="deliver",
            permanent_statuses=_PERMANENT_STATUSES,
        )

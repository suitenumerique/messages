"""Async HTTP client for the MDA inbound API.

The Postfix milter uses ``requests`` (sync, see ``src/api/mda.py``). pymta
runs in an asyncio event loop, where a blocking call would freeze the whole
server, so the same JWT contract is implemented here on httpx.

* ``POST /inbound/mta/check/``: ``application/json`` body
  ``{"addresses": [...]}``, answered with ``{addr: bool}``.
* ``POST /inbound/mta/deliver/``: an ``ENVELOPED_CONTENT_TYPE`` body, the
  envelope (sender, recipients, client info, size) as one line of JSON, a
  newline, then the message. The milter still sends the older form,
  ``message/rfc822`` with the envelope as JWT claims, which the MDA accepts
  too; that one is capped by the size of an HTTP header.

Every request carries a short-lived HS256 JWT whose ``body_hash`` claim binds
it to the bytes posted.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import jwt

from . import metrics, settings

logger = logging.getLogger(__name__)

# Hosts a plaintext MDA URL is accepted for without a warning.
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
# A shorter HS256 secret can be brute-forced. Warned about at startup, not
# refused: the dev secret is shorter.
_MIN_SECRET_LENGTH = 32

# Statuses on which ``deliver`` rejects a message for good (SMTP 5xx). An
# allow-list: deferring costs a retry, bouncing loses the mail.
#
#   400: the MDA could not parse the message, or the request was malformed.
#   413: over the MDA's size limit.
#
# Only with the MDA's own body (``"status": "error"``): the same status from a
# proxy in front of it says nothing about the message.
#
# Everything else defers:
#   415: an MDA older than this client, mid-rollout.
#   207: some recipients were delivered. The MDA cannot name the others to
#         us, so the sender retries the whole envelope: duplicates for some
#         instead of a loss for the rest.
#   401 / 403: secret rotation, or an ``exp`` the MDA's clock reads as past.
#   404: a routing or deployment mistake.
#   429: throttling.
#
# Not used for ``check``, which carries no message: a 400 or 413 there is a
# fault in the request we built, and defers.
_PERMANENT_STATUSES = frozenset({400, 413})

# Content-Type of a deliver request whose body is the envelope as one line of
# JSON, a newline, then the message. Must match the MDA's own constant.
ENVELOPED_CONTENT_TYPE = "application/vnd.stmsg.mta-delivery"


# A request body is handed to httpx in pieces of this size. Over TLS, what is
# handed over in one piece is encrypted in one piece: for a whole message,
# two more copies of it.
_BODY_CHUNK = 65536


async def _stream(parts: tuple[bytes, ...]) -> AsyncIterator[bytes]:
    for part in parts:
        for start in range(0, len(part), _BODY_CHUNK):
            yield part[start : start + _BODY_CHUNK]


def _json_object(response: httpx.Response, endpoint_label: str) -> dict:
    """The body as a JSON object, or ``{}`` for anything else.

    Some error bodies are HTML, a list or empty: callers can ``.get()`` on
    the result whatever came back.
    """
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        # JSONDecodeError, and UnicodeDecodeError for a body that is not
        # UTF-8 (a proxy's latin-1 error page).
        return {}
    if isinstance(payload, dict):
        return payload
    # Debug: the caller logs the consequence (mda_check_no_verdict,
    # mda_unhealthy, mda_unexpected_status) with the session attached.
    logger.debug(
        "mda_body_not_an_object",
        extra={
            "endpoint": endpoint_label,
            "status": response.status_code,
            "body_type": type(payload).__name__,
        },
    )
    return {}


@dataclass(frozen=True)
class MDAResult:
    """Result of an MDA call.

    ``ok``: the call returned HTTP 200. ``temp_fail``: try again later
    (network error, timeout, 5xx, and every status the endpoint does not name
    as permanent); only ``deliver`` names any, in ``_PERMANENT_STATUSES``.

    ``payload`` is always a dict: a body that is absent, unparseable or not a
    JSON object becomes ``{}``. An empty payload means "no usable answer", so
    callers must not read a missing key as a negative one.
    """

    ok: bool
    temp_fail: bool
    payload: dict
    status_code: int

    @classmethod
    def unanswered(cls) -> MDAResult:
        """No HTTP response was read: try again later."""
        return cls(ok=False, temp_fail=True, payload={}, status_code=0)


class MDAClient:
    """Async client for the MDA REST API, one per server process.

    One ``httpx.AsyncClient`` is kept, so its connections outlive SMTP
    sessions. An SMTP transaction waits for the MDA's answer: there is no
    queue on disk.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
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
        # Reset by a successful call, while the breaker is closed or by its probe.
        self._consecutive_failures = 0
        # When the open breaker's cooldown ends, on ``clock``. None when closed.
        self._open_until: float | None = None
        # True while the one call let through after a cooldown is in flight.
        self._probing = False
        # Counts the times an open breaker was closed: see _record_failure.
        self._generation = 0
        self._client: httpx.AsyncClient | None = None
        self._validate_credentials()

    def _validate_credentials(self) -> None:
        """Refuse a missing secret; warn about a weak one or a plaintext non-local MDA URL.

        Warnings only for the last two: the dev secret is 20 characters and dev
        deployments reach the MDA over the docker bridge without TLS. See the
        production checklist in the README.
        """
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and host not in _LOCAL_HOSTNAMES:
            logger.warning(
                "mda_url_plaintext",
                extra={
                    "host": host,
                    "detail": "the JWT bearer token will cross the network in clear; use https",
                },
            )
        if not self.secret:
            # No MDA call could be signed.
            logger.error(
                "mda_secret_missing",
                extra={"detail": "no MDA call can be signed; refusing to start"},
            )
            raise ValueError("MDA_API_SECRET is not set; pymta cannot sign its calls to the MDA")
        if len(self.secret) < _MIN_SECRET_LENGTH:
            logger.warning(
                "mda_secret_weak",
                # The length is not logged: it would narrow the search for
                # someone who reads the log and captures a JWT.
                extra={
                    "minimum": _MIN_SECRET_LENGTH,
                    "detail": "short HS256 secrets are brute-forceable from one captured JWT",
                },
            )

    async def start(self) -> httpx.AsyncClient:
        """Open the persistent HTTP client. Idempotent."""
        if self._client is None:
            # A session makes one call at a time. None when sessions are not
            # capped. As many are kept alive: fewer, and a busy server opens
            # a connection per call. Idle ones expire after 5 s.
            sessions = settings.PYMTA_MAX_SESSIONS_TOTAL or None
            limits = httpx.Limits(max_keepalive_connections=sessions, max_connections=sessions)
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=limits,
                # httpx's default would let HTTP_PROXY / HTTPS_PROXY / ALL_PROXY
                # in the environment route these calls, JWT and message
                # included, through a proxy. This stack has a socks-proxy
                # service, so such a variable can reach a shared env file.
                trust_env=False,
                # Stated: a followed redirect would send the Authorization
                # header to the host the response names, where the body_hash
                # claim still validates.
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_jwt(self, body: bytes | tuple[bytes, ...], metadata: dict) -> str:
        digest = hashlib.sha256()
        for part in (body,) if isinstance(body, bytes) else body:
            digest.update(part)
        # Metadata first, so a key named "exp" or "body_hash" in it cannot
        # replace ours.
        claims = {
            **metadata,
            "exp": datetime.datetime.now(tz=datetime.UTC)
            + datetime.timedelta(seconds=self.jwt_ttl),
            "body_hash": digest.hexdigest(),
        }
        return jwt.encode(claims, self.secret, algorithm="HS256")

    def _breaker_open(self) -> bool:
        """Whether this call is shed. Starts the probe when the cooldown is over."""
        if self._open_until is None:
            return False
        if self._clock() < self._open_until:
            return True
        # Cooldown over: one call goes through as a probe and the others are
        # shed until it has its answer. All of them going through would cost a
        # full threshold of timeouts each time the MDA is still down.
        if self._probing:
            return True
        self._probing = True
        return False

    def breaker_is_open(self) -> bool:
        """Whether calls are being shed right now, for the metrics gauge.

        Without the side effect of ``_breaker_open``. Computed at scrape time:
        a stored value would keep reading 1 after the cooldown of an idle
        process, with no call to clear it.
        """
        if self._open_until is None:
            return False
        return self._clock() < self._open_until or self._probing

    def _record_failure(self, probe: bool, generation: int) -> None:
        """Count a failed call.

        ``probe``: it was the one let through after a cooldown.
        ``generation``: ``_generation`` when the call was let through.
        """
        if not self._breaker_threshold:
            return
        if generation != self._generation:
            # Sent during an outage that has ended since: says nothing of
            # the MDA as it is now.
            return
        if probe:
            # The probe failed: same outage, another cooldown. Not on the
            # failure of a call that was in flight before the breaker opened.
            self._open_until = self._clock() + self._breaker_cooldown
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold and self._open_until is None:
            self._open_until = self._clock() + self._breaker_cooldown
            logger.warning(
                "mda_breaker_opened",
                extra={
                    "consecutive_failures": self._consecutive_failures,
                    "cooldown_seconds": self._breaker_cooldown,
                },
            )

    def _record_success(self, probe: bool) -> None:
        if self._open_until is not None and not probe:
            # In flight since before the breaker opened: the probe decides.
            return
        if self._consecutive_failures:
            logger.info("mda_recovered", extra={"after_failures": self._consecutive_failures})
        self._consecutive_failures = 0
        if self._open_until is not None:
            self._generation += 1
        self._open_until = None
        self._probing = False

    async def _post(  # noqa: PLR0913
        self,
        path: str,
        content_type: str,
        # Several parts are sent one after the other as a single body.
        body: bytes | tuple[bytes, ...],
        *,
        endpoint_label: str,
        permanent_statuses: frozenset[int] = frozenset(),
        # Id of the SMTP session this call serves. Logged here, so a failure
        # can be joined to the mail it affected, and sent as the
        # ``mta_session`` claim so the MDA's lines join up too.
        session: str | None = None,
        # Seconds the whole call may take. None: the caller bounds it.
        deadline: float | None = None,
    ) -> MDAResult:
        if self._breaker_open():
            metrics.MDA_REQUEST_DURATION.labels(
                endpoint=endpoint_label, result="breaker_open"
            ).observe(0)
            return MDAResult.unanswered()
        # Past the check with the flag up, this call is the probe.
        probe = self._probing
        generation = self._generation
        try:
            result, healthy = await self._send(
                path, content_type, body, endpoint_label, permanent_statuses, session, deadline
            )
            if healthy:
                self._record_success(probe)
            elif healthy is not None:
                self._record_failure(probe, generation)
            return result
        finally:
            # A probe that ends with no verdict (cancelled by the DATA
            # deadline, refused by our own pool) gives its turn to the next.
            if probe:
                self._probing = False

    async def _send(  # noqa: PLR0913, PLR0917
        self,
        path: str,
        content_type: str,
        body: bytes | tuple[bytes, ...],
        endpoint_label: str,
        permanent_statuses: frozenset[int],
        session: str | None,
        deadline: float | None,
    ) -> tuple[MDAResult, bool | None]:
        """One request. Returns (result, healthy).

        ``healthy`` is what the breaker counts: True for an answer from a
        live MDA, False for a failure of it, None when the call says nothing
        about it.
        """
        client = self._client or await self.start()

        url = self.base_url + path.lstrip("/")
        # A claim, not a header: it is signed, so nothing between us and the
        # MDA can change it. Named ``mta_session`` because the MDA has Django
        # sessions of its own.
        token = self._build_jwt(body, {"mta_session": session} if session else {})
        headers = {"Content-Type": content_type, "Authorization": f"Bearer {token}"}
        content: bytes | AsyncIterator[bytes] = body if isinstance(body, bytes) else _stream(body)
        if not isinstance(body, bytes):
            # Stated, or httpx would send a streamed body chunked.
            headers["Content-Length"] = str(sum(len(part) for part in body))

        start = self._clock()
        try:
            # httpx's timeout is per phase (connect, each read, each write):
            # an MDA that drips its answer never meets it.
            async with asyncio.timeout(deadline):
                response = await client.post(url, content=content, headers=headers)
        except httpx.PoolTimeout:
            # No free connection in our own pool. Says nothing about the MDA,
            # so it does not feed the breaker.
            metrics.MDA_REQUEST_DURATION.labels(
                endpoint=endpoint_label, result="pool_timeout"
            ).observe(self._clock() - start)
            logger.warning(
                "mda_pool_exhausted", extra={"endpoint": endpoint_label, "session_id": session}
            )
            return MDAResult.unanswered(), None
        except httpx.TimeoutException, TimeoutError:
            # Counted by the breaker, on ``deliver`` too: a timeout is no
            # verdict on the message, and an MDA that hangs must be shed.
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="timeout").observe(
                self._clock() - start
            )
            logger.warning(
                "mda_timeout",
                extra={
                    "endpoint": endpoint_label,
                    "session_id": session,
                    "elapsed_seconds": round(self._clock() - start, 3),
                    "limit_seconds": self.timeout,
                },
            )
            return MDAResult.unanswered(), False
        except httpx.HTTPError:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="error").observe(
                self._clock() - start
            )
            # WARNING: the fault is the MDA's or the network's, not ours.
            logger.warning(
                "mda_transport_error",
                exc_info=True,
                extra={"endpoint": endpoint_label, "session_id": session},
            )
            return MDAResult.unanswered(), False

        elapsed = self._clock() - start

        payload = _json_object(response, endpoint_label)
        status = response.status_code
        if status == 200:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="ok").observe(
                elapsed
            )
            return MDAResult(ok=True, temp_fail=False, payload=payload, status_code=status), True

        # The MDA's own answer about this message, not an error page from
        # something in front of it (an ingress answers 413 too).
        verdict = payload.get("status") == "error"
        # Two separate decisions:
        #  * temp: what the sender is told. Permanent only for a permanent
        #    status said by the MDA itself.
        #  * unhealthy: what the breaker counts, with the transport failures
        #    above. A 5xx that is a verdict is left out, or one sender's
        #    messages could open the breaker for everyone; the MDA answers its
        #    own outages with a 503 that is not one. A 207 or a 401 is an
        #    answer from a live MDA and closes the breaker.
        temp = not (verdict and status in permanent_statuses)
        unhealthy = status >= 500 and not verdict
        if status >= 500:
            result_label = "http_5xx"
        else:
            result_label = "http_defer" if temp else "http_perm"
        metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result=result_label).observe(
            elapsed
        )
        fields = {
            "endpoint": endpoint_label,
            "session_id": session,
            "status": status,
            "outcome": "defer" if temp else "reject",
        }
        result = MDAResult(ok=False, temp_fail=temp, payload=payload, status_code=status)
        if unhealthy:
            logger.warning("mda_unhealthy", extra=fields)
            return result, False
        if verdict:
            # Debug: a sender can cause this line at will.
            logger.debug("mda_refused", extra=fields)
        else:
            # A 207, a 401, an ingress's 413.
            logger.warning("mda_unexpected_status", extra=fields)
        return result, True

    async def check_recipient(self, address: str, session: str | None = None) -> MDAResult:
        """Ask the MDA whether a single recipient mailbox exists."""
        body = json.dumps({"addresses": [address]}, separators=(",", ":")).encode("utf-8")
        return await self._post(
            "inbound/mta/check/",
            "application/json",
            body,
            endpoint_label="check",
            session=session,
            # ``deliver`` is bounded by the DATA deadline. Nothing else
            # bounds a check short of PYMTA_COMMAND_TIMEOUT.
            deadline=self.timeout,
        )

    async def deliver(  # noqa: PLR0913
        self,
        *,
        message: bytes,
        sender: str,
        original_recipients: list[str],
        client_address: str | None,
        client_port: str | None,
        client_helo: str | None,
        session: str | None = None,
        transport: dict | None = None,
    ) -> MDAResult:
        """Post the message to the MDA and wait for its delivery.

        The envelope is one line of JSON ahead of the message, not JWT claims:
        100 recipients do not fit in an HTTP header (8190 bytes on gunicorn).
        ``body_hash`` covers the whole body, so the envelope is signed too.

        ``transport`` is how the message arrived (``protocol``, ``tls_version``,
        ``tls_cipher``, ``server_name``), for the MDA's ``Received`` header.
        """
        envelope = {
            "sender": sender,
            "original_recipients": list(original_recipients),
            "client_address": client_address,
            "client_port": client_port,
            "client_helo": client_helo,
            "size": str(len(message)),
            **(transport or {}),
        }
        # json.dumps escapes newlines, so the first LF of the body is the
        # separator. Two parts, so the message is not copied into a joined body.
        head = json.dumps(envelope, separators=(",", ":")).encode("ascii") + b"\n"
        return await self._post(
            "inbound/mta/deliver/",
            ENVELOPED_CONTENT_TYPE,
            (head, message),
            endpoint_label="deliver",
            permanent_statuses=_PERMANENT_STATUSES,
            session=session,
        )

"""Async HTTP client for the MDA inbound API.

The Postfix milter uses ``requests`` (sync, see ``src/api/mda.py``). pymta
runs inside an asyncio event loop, so blocking HTTP calls would freeze the
whole SMTP server; we mirror the same JWT contract here on top of httpx.

The MDA contract — kept identical to the milter so both implementations stay
swap-compatible — is:

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

import httpx
import jwt

from . import metrics, settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MDAResult:
    """Result of an MDA call.

    ``ok`` is true iff the call returned HTTP 200 with a JSON body that the
    caller can rely on. ``temp_fail`` distinguishes "try again later" (network
    error / 5xx / timeout) from a permanent rejection. ``payload`` is the
    decoded JSON body when available.
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

    def __init__(
        self,
        base_url: str | None = None,
        secret: str | None = None,
        timeout: int | None = None,
    ):
        self.base_url = (base_url or settings.MDA_API_BASE_URL).rstrip("/") + "/"
        self.secret = secret or settings.MDA_API_SECRET
        self.timeout = timeout if timeout is not None else settings.MDA_API_TIMEOUT
        self._client: httpx.AsyncClient | None = None

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
        claims = {
            "exp": datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(seconds=60),
            "body_hash": hashlib.sha256(body).hexdigest(),
            **metadata,
        }
        return jwt.encode(claims, self.secret, algorithm="HS256")

    async def _post(
        self,
        path: str,
        content_type: str,
        body: bytes,
        metadata: dict,
        endpoint_label: str,
    ) -> MDAResult:
        client = self._client or await self.start()

        url = self.base_url + path.lstrip("/")
        token = self._build_jwt(body, metadata)
        headers = {"Content-Type": content_type, "Authorization": f"Bearer {token}"}

        start = time.monotonic()
        try:
            response = await client.post(url, content=body, headers=headers)
        except httpx.TimeoutException:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="timeout").observe(
                time.monotonic() - start
            )
            logger.warning("MDA %s timeout after %.2fs", endpoint_label, time.monotonic() - start)
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=0)
        except httpx.HTTPError:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="error").observe(
                time.monotonic() - start
            )
            logger.exception("MDA %s transport error", endpoint_label)
            return MDAResult(ok=False, temp_fail=True, payload={}, status_code=0)

        elapsed = time.monotonic() - start

        # JSON decode is best-effort; some error bodies may be HTML.
        try:
            payload = response.json() if response.content else {}
        except json.JSONDecodeError:
            payload = {}

        status = response.status_code
        if status == 200:
            metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result="ok").observe(
                elapsed
            )
            return MDAResult(ok=True, temp_fail=False, payload=payload, status_code=status)

        # 5xx → tempfail; 4xx → permanent reject.
        temp = status >= 500
        result_label = "http_5xx" if temp else "http_4xx"
        metrics.MDA_REQUEST_DURATION.labels(endpoint=endpoint_label, result=result_label).observe(
            elapsed
        )
        logger.warning("MDA %s returned HTTP %d", endpoint_label, status)
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
        )

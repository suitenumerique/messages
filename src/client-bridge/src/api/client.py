"""Async HTTP client for the Messages API."""

import logging
import time

import httpx
import jwt

logger = logging.getLogger(__name__)

# Default transport with retry support
_RETRY_STATUS_CODES = {502, 503, 504}
_MAX_RETRIES = 3


class SessionExpired(Exception):
    """Raised when the JWT session token has expired."""


class MessagesAPIClient:
    """Async client for interacting with the Messages API over HTTP."""

    def __init__(self, base_url: str, api_secret: str = ""):
        self.base_url = base_url.rstrip("/")
        headers = {}
        if api_secret:
            headers["X-Service-Auth"] = f"Bearer {api_secret}"
        transport = httpx.AsyncHTTPTransport(retries=_MAX_RETRIES)
        self._client = httpx.AsyncClient(
            headers=headers,
            transport=transport,
            timeout=30.0,
        )
        self._token: str | None = None
        self._token_exp: float | None = None

    def with_token(self, token: str) -> "MessagesAPIClient":
        """Return a shallow copy whose requests include the session JWT.

        The returned client shares the same underlying httpx.AsyncClient
        but adds X-Channel-Token to all requests so that
        ClientBridgeChannelAuthentication resolves the request to the
        channel's user.

        Also stores the token's expiration so we can fail fast with a
        clear error instead of waiting for a 401 from the backend.
        """
        clone = object.__new__(MessagesAPIClient)
        clone.base_url = self.base_url
        clone._client = self._client  # noqa: SLF001
        clone._token = token
        # Extract exp from the JWT (decode without verification — we issued it)
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            clone._token_exp = payload.get("exp")
        except jwt.InvalidTokenError:
            clone._token_exp = None
        return clone

    def _check_token(self) -> dict:
        """Return X-Channel-Token header, or raise SessionExpired."""
        if self._token is None:
            return {}
        if self._token_exp is not None and time.time() >= self._token_exp:
            raise SessionExpired("Session token has expired. Please re-authenticate.")
        return {"X-Channel-Token": self._token}

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def authenticate_channel(self, username: str, password: str) -> dict | None:
        """Authenticate a channel by mailbox email and app-specific password.

        Returns the decoded JWT payload with an extra ``token`` key
        (the raw JWT string) if authentication succeeds, or None if it fails.
        """
        resp = await self._client.post(
            f"{self.base_url}/client-bridge/auth/",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        token = resp.json()["token"]
        # Decode without verification — the backend already signed it and
        # we just need the payload fields (channel_id, mailbox_id, role).
        payload = jwt.decode(token, options={"verify_signature": False})
        payload["token"] = token
        return payload

    async def submit_message(
        self,
        token: str,
        mail_from: str,
        rcpt_to: str,
        raw_message: bytes,
    ) -> dict | None:
        """Submit an outbound message through the client-bridge endpoint.

        Posts the raw RFC 5322 message to the backend for delivery.
        Uses the session JWT for authentication.
        Returns the response dict on success, or None on failure.
        """
        resp = await self._client.post(
            f"{self.base_url}/client-bridge/submit/",
            content=raw_message,
            headers={
                "Content-Type": "message/rfc822",
                "X-Channel-Token": token,
                "X-Mail-From": mail_from,
                "X-Rcpt-To": rcpt_to,
            },
            timeout=30,
        )
        if resp.status_code in (200, 202):
            return resp.json()
        return None

    async def list_threads(
        self, mailbox_id: str, folder: str = "inbox", page: int = 1, page_size: int = 100
    ) -> dict:
        """List threads for a mailbox, filtered by folder."""
        params = {
            "mailbox_id": mailbox_id,
            "page": page,
            "page_size": page_size,
        }
        if folder == "trash":
            params["is_trashed"] = "1"
        elif folder == "drafts":
            params["is_draft"] = "1"
        elif folder == "spam":
            params["is_spam"] = "1"
        elif folder == "archive":
            params["is_archived"] = "1"
        elif folder == "sent":
            params["is_sender"] = "1"
        elif folder == "starred":
            params["is_starred"] = "1"

        resp = await self._client.get(
            f"{self.base_url}/threads/",
            params=params,
            headers=self._check_token(),
        )
        resp.raise_for_status()
        return resp.json()

    async def list_messages(self, thread_id: str, mailbox_id: str) -> list[dict]:
        """List all messages in a thread."""
        resp = await self._client.get(
            f"{self.base_url}/messages/",
            params={"thread_id": thread_id, "mailbox_id": mailbox_id},
            headers=self._check_token(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data) if isinstance(data, dict) else data

    async def get_message_eml(self, message_id: str) -> bytes:
        """Download a message as raw RFC 5322 EML."""
        resp = await self._client.get(
            f"{self.base_url}/messages/{message_id}/eml",
            headers=self._check_token(),
        )
        resp.raise_for_status()
        return resp.content

    async def update_message_flags(self, message_id: str, **flags) -> bool:
        """Update message flags (is_starred, is_trashed, is_archived, is_spam)."""
        resp = await self._client.patch(
            f"{self.base_url}/messages/{message_id}/",
            json=flags,
            headers=self._check_token(),
            timeout=10,
        )
        return resp.status_code == 200

    async def update_thread_flags(self, thread_id: str, mailbox_id: str, **flags) -> bool:
        """Update thread flags for a mailbox."""
        resp = await self._client.patch(
            f"{self.base_url}/threads/{thread_id}/",
            json={"mailbox_id": mailbox_id, **flags},
            headers=self._check_token(),
            timeout=10,
        )
        return resp.status_code == 200

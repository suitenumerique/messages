"""Async HTTP client for the Messages API.

Every request carries the email client's credentials (mailbox address + app
password) in X-Mailbox-Email / X-App-Password: the backend checks them each
time, so a rotated password or a paused channel takes effect at once.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3

# App password scopes (backend ``ChannelApiKeyScope``) the bridge checks at login.
SCOPE_MESSAGES_READ = "messages:read"
SCOPE_THREADS_WRITE = "threads:write"
SCOPE_MESSAGES_SEND = "messages:send"


class CredentialsRevoked(Exception):
    """Raised when the backend refuses the credentials (rotated, paused...)."""


class SubmissionRejected(Exception):
    """Raised when the backend refuses a submitted message for good."""


def credential_headers(email: str, password: str) -> dict[str, str]:
    """Headers authenticating a request as the given app password."""
    return {"X-Mailbox-Email": email, "X-App-Password": password}


class MessagesAPIClient:
    """Async client for interacting with the Messages API over HTTP."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers=headers or {},
            transport=httpx.AsyncHTTPTransport(retries=_MAX_RETRIES),
            timeout=30.0,
        )

    def with_credentials(self, email: str, password: str) -> "MessagesAPIClient":
        """Return a new client whose requests carry these credentials."""
        return MessagesAPIClient(self.base_url, credential_headers(email, password))

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()

    @staticmethod
    def _check(resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise CredentialsRevoked("The app password is no longer valid.")
        resp.raise_for_status()
        return resp

    async def authenticate_channel(self, email: str, password: str) -> dict | None:
        """Check an app password.

        Returns ``{"channel_id", "mailbox_id", "mailbox_email", "scopes"}``,
        or None if the credentials are refused.
        """
        resp = await self._client.get(
            f"{self.base_url}/client-bridge/channel/",
            headers=credential_headers(email, password),
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        return resp.json()

    async def submit_message(
        self,
        email: str,
        password: str,
        mailbox_id: str,
        rcpt_to: str,
        raw_message: bytes,
    ) -> dict | None:
        """Submit an outbound message through the backend /submit/ endpoint.

        Returns the response dict on success, or None on a temporary failure.
        Raises ``CredentialsRevoked`` on a 401 and ``SubmissionRejected`` when
        the backend refuses the message for good (another 4xx: bad sender,
        Bcc header, size...).
        """
        resp = await self._client.post(
            f"{self.base_url}/submit/",
            content=raw_message,
            headers={
                **credential_headers(email, password),
                "Content-Type": "message/rfc822",
                "X-Mail-From": mailbox_id,
                "X-Rcpt-To": rcpt_to,
            },
            timeout=30,
        )
        if resp.status_code in (200, 202):
            return resp.json()
        if resp.status_code == 401:
            raise CredentialsRevoked("The app password is no longer valid.")
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            try:
                detail = resp.json().get("detail", "")
            except ValueError:
                detail = ""
            raise SubmissionRejected(str(detail) or f"HTTP {resp.status_code}")
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
            params["has_trashed"] = "1"
        elif folder == "drafts":
            params["has_draft"] = "1"
        elif folder == "spam":
            params["is_spam"] = "1"
        elif folder == "archive":
            params["has_archived"] = "1"
        elif folder == "sent":
            params["has_sender"] = "1"
        elif folder == "starred":
            params["has_starred"] = "1"

        resp = await self._client.get(f"{self.base_url}/threads/", params=params)
        return self._check(resp).json()

    async def list_messages(self, thread_id: str, mailbox_id: str) -> list[dict]:
        """List all messages in a thread."""
        resp = await self._client.get(
            f"{self.base_url}/messages/",
            params={"thread_id": thread_id, "mailbox_id": mailbox_id},
        )
        data = self._check(resp).json()
        return data.get("results", data) if isinstance(data, dict) else data

    async def get_message_eml(self, message_id: str) -> bytes:
        """Download a message as raw RFC 5322 EML."""
        resp = await self._client.get(f"{self.base_url}/messages/{message_id}/eml/")
        return self._check(resp).content

    async def change_flag(
        self,
        flag: str,
        value: bool,
        mailbox_id: str,
        *,
        message_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
        read_at: str | None = None,
    ) -> bool:
        """Change a flag on messages or threads via POST /flag/.

        Supported flags: unread, starred, trashed, archived, spam.
        For 'unread', read_at must be provided (ISO 8601 timestamp or null).
        """
        payload: dict = {
            "flag": flag,
            "value": value,
            "mailbox_id": mailbox_id,
        }
        if message_ids:
            payload["message_ids"] = message_ids
        if thread_ids:
            payload["thread_ids"] = thread_ids
        if flag == "unread":
            payload["read_at"] = read_at
        resp = await self._client.post(f"{self.base_url}/flag/", json=payload, timeout=10)
        if resp.status_code == 401:
            raise CredentialsRevoked("The app password is no longer valid.")
        return resp.status_code == 200

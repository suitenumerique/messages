"""SMTP submission handler using aiosmtpd.

Authenticates clients with their app password and submits outbound
messages through the backend /submit/ endpoint.
"""

import logging

import httpx
from aiosmtpd.smtp import AuthResult, LoginPassword, SMTP

from .api.client import (
    SCOPE_MESSAGES_SEND,
    CredentialsRevoked,
    MessagesAPIClient,
    SubmissionRejected,
    credential_headers,
    log_id,
)

logger = logging.getLogger(__name__)


class SubmissionAuthenticator:
    """Synchronous authenticator callback for aiosmtpd.

    aiosmtpd's ``_authenticate`` is not async-safe — it calls the
    authenticator synchronously and does not await coroutines.
    We therefore use ``httpx.Client`` (sync) for the auth HTTP call.
    """

    def __init__(self, api_client: MessagesAPIClient):
        self._api_url = api_client.base_url

    def __call__(self, server: SMTP, session, envelope, mechanism, auth_data):
        if not isinstance(auth_data, LoginPassword):
            return AuthResult(success=False, handled=False)

        username = auth_data.login.decode("utf-8", errors="replace")
        password = auth_data.password.decode("utf-8", errors="replace")
        if not password:
            return AuthResult(success=False, handled=False)

        # Sync HTTP call — aiosmtpd's _authenticate is not async
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    f"{self._api_url}/client-bridge/channel/",
                    headers=credential_headers(username, password),
                )
        except httpx.HTTPError:
            logger.exception("SMTP auth HTTP error for login %s", log_id(username))
            return AuthResult(success=False, handled=False)

        if resp.status_code != 200:
            logger.warning("SMTP auth failed for login %s", log_id(username))
            return AuthResult(success=False, handled=False)

        channel = resp.json()
        if SCOPE_MESSAGES_SEND not in (channel.get("scopes") or []):
            logger.warning(
                "SMTP auth rejected for channel %s: no send access", channel.get("channel_id")
            )
            return AuthResult(success=False, handled=False)

        logger.info("SMTP auth success for channel_id=%s", channel.get("channel_id"))
        # Kept for the session: every submission carries the credentials.
        return AuthResult(
            success=True,
            handled=False,
            auth_data={**channel, "email": username, "password": password},
        )


class SubmissionHandler:
    """aiosmtpd handler that processes submitted messages
    by forwarding them to the Messages API."""

    def __init__(self, api_client: MessagesAPIClient):
        self.api_client = api_client

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """Accept any recipient address."""
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        """Forward the submitted message to the Messages API."""
        auth_data = session.auth_data
        if not auth_data:
            return "530 5.7.0 Authentication required"

        rcpt_to = ",".join(envelope.rcpt_tos) if envelope.rcpt_tos else ""
        if not rcpt_to:
            return "554 5.1.1 No recipients"

        channel_id = auth_data.get("channel_id")
        try:
            result = await self.api_client.submit_message(
                email=auth_data["email"],
                password=auth_data["password"],
                mailbox_id=auth_data.get("mailbox_id", ""),
                rcpt_to=rcpt_to,
                raw_message=envelope.content,
            )
        except CredentialsRevoked:
            logger.warning("App password no longer valid for channel %s", channel_id)
            return "535 5.7.8 Authentication credentials invalid"
        except SubmissionRejected as exc:
            logger.warning("Message rejected for channel %s: %s", channel_id, exc)
            return "554 5.7.1 Message rejected"

        if result is None:
            logger.error("Message submission failed for channel %s", channel_id)
            return "451 4.3.0 Message submission failed"

        msg_id = result.get("message_id", "unknown")
        logger.info("Message submitted: id=%s, channel=%s", msg_id, channel_id)
        return f"250 OK id={msg_id}"

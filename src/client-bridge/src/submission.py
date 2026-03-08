"""SMTP submission handler using aiosmtpd.

Authenticates clients via the Messages API and submits outbound
messages through the client-bridge endpoint.
"""

import logging
import time

import httpx
import jwt
from aiosmtpd.smtp import AuthResult, LoginPassword, SMTP

from .api.client import MessagesAPIClient

logger = logging.getLogger(__name__)


class SubmissionAuthenticator:
    """Synchronous authenticator callback for aiosmtpd.

    aiosmtpd's ``_authenticate`` is not async-safe — it calls the
    authenticator synchronously and does not await coroutines.
    We therefore use ``httpx.Client`` (sync) for the auth HTTP call.
    """

    def __init__(self, api_client: MessagesAPIClient):
        self._api_url = api_client.base_url
        self._headers = dict(api_client._client.headers)  # noqa: SLF001

    def __call__(self, server: SMTP, session, envelope, mechanism, auth_data):
        if not isinstance(auth_data, LoginPassword):
            return AuthResult(success=False, handled=False)

        username = auth_data.login.decode("utf-8", errors="replace")
        password = auth_data.password.decode("utf-8", errors="replace")

        # Sync HTTP call — aiosmtpd's _authenticate is not async
        try:
            with httpx.Client(headers=self._headers, timeout=10) as client:
                resp = client.post(
                    f"{self._api_url}/client-bridge/auth/",
                    json={"username": username, "password": password},
                )
        except httpx.HTTPError:
            logger.exception("SMTP auth HTTP error for %s", username)
            return AuthResult(success=False, handled=False)

        if resp.status_code != 200:
            logger.warning("SMTP auth failed for %s", username)
            return AuthResult(success=False, handled=False)

        token = resp.json().get("token")
        if not token:
            logger.warning("SMTP auth: no token in response for %s", username)
            return AuthResult(success=False, handled=False)

        # Decode JWT without verification — we just need the payload fields
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
        except jwt.InvalidTokenError:
            logger.warning("SMTP auth: invalid JWT for %s", username)
            return AuthResult(success=False, handled=False)
        payload["token"] = token

        # Reject channels without send access
        role = payload.get("role", "sender")
        if role not in ("sender", "sender_only"):
            logger.warning("SMTP auth rejected for %s: role %r has no send access", username, role)
            return AuthResult(success=False, handled=False)

        logger.info("SMTP auth success for %s (role=%s)", username, role)
        return AuthResult(
            success=True,
            handled=False,
            auth_data=payload,
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

        token = auth_data.get("token")
        if not token:
            return "451 Internal error: missing session token"

        # Check token expiry before hitting the backend
        exp = auth_data.get("exp")
        if exp and time.time() >= exp:
            logger.warning(
                "SMTP session token expired for channel %s", auth_data.get("channel_id")
            )
            return "421 4.7.0 Session expired, please re-authenticate"

        mail_from = envelope.mail_from or ""
        rcpt_to = ",".join(envelope.rcpt_tos) if envelope.rcpt_tos else ""

        if not rcpt_to:
            return "554 5.1.1 No recipients"

        result = await self.api_client.submit_message(
            token=token,
            mail_from=mail_from,
            rcpt_to=rcpt_to,
            raw_message=envelope.content,
        )

        if result is None:
            logger.error(
                "Message submission failed for channel %s",
                auth_data.get("channel_id"),
            )
            return "451 4.3.0 Message submission failed"

        msg_id = result.get("message_id", "unknown")
        logger.info("Message submitted: id=%s, channel=%s", msg_id, auth_data.get("channel_id"))
        return f"250 OK id={msg_id}"

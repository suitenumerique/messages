"""Pymap backend that uses the Messages API as its data store."""

from __future__ import annotations

import logging
from argparse import ArgumentParser, Namespace
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from secrets import token_bytes
from typing import Any, Final

from pymap.config import BackendCapability, IMAPConfig
from pymap.exceptions import InvalidAuth, UserNotFound
from pymap.health import HealthStatus
from pymap.interfaces.backend import BackendInterface
from pymap.interfaces.login import IdentityInterface, LoginInterface
from pymap.interfaces.token import TokenCredentials
from pymap.token import AllTokens
from pymap.user import UserMetadata
from pysasl.creds.plain import PlainCredentials
from pysasl.creds.server import ServerCredentials

from .api.client import MessagesAPIClient
from .mailbox import MailboxSet
from .session import Session

__all__ = ["MessagesBackend"]

logger = logging.getLogger(__name__)


class MessagesBackend(BackendInterface):
    """Pymap backend that delegates to the Messages API over HTTP."""

    def __init__(self, login: Login, config: Config) -> None:
        super().__init__()
        self._login = login
        self._config = config
        self._status = HealthStatus()

    @property
    def login(self) -> Login:
        return self._login

    @property
    def config(self) -> Config:
        return self._config

    @property
    def status(self) -> HealthStatus:
        return self._status

    @classmethod
    def add_subparser(cls, name: str, subparsers: Any) -> ArgumentParser:
        parser: ArgumentParser = subparsers.add_parser(name, help="Messages API backend")
        from src import settings

        parser.add_argument(
            "--api-url",
            default=settings.MESSAGES_API_BASE_URL,
            metavar="URL",
            help="Base URL for the Messages API",
        )
        return parser

    @classmethod
    async def init(cls, args: Namespace, **overrides: Any) -> tuple[MessagesBackend, Config]:
        config = Config.from_args(args, **overrides)
        api_secret = getattr(args, "api_secret", "") or ""
        api_client = MessagesAPIClient(config.api_url, api_secret=api_secret)
        login = Login(config, api_client)
        return cls(login, config), config

    async def start(self, stack: AsyncExitStack) -> None:
        # Register the shared API client for cleanup so HTTP connections
        # are properly closed on shutdown.
        stack.push_async_callback(self._login.api_client.close)


class Config(IMAPConfig):
    """Config for the Messages API backend."""

    def __init__(
        self, args: Namespace, *, api_url: str, admin_key: bytes | None = None, **extra: Any
    ) -> None:
        admin_key = admin_key or token_bytes()
        super().__init__(args, admin_key=admin_key, **extra)
        self._api_url = api_url

    @property
    def backend_capability(self) -> BackendCapability:
        return BackendCapability(idle=False, object_id=True, multi_append=False)

    @property
    def api_url(self) -> str:
        return self._api_url

    @classmethod
    def parse_args(cls, args: Namespace) -> dict[str, Any]:
        return {**super().parse_args(args), "api_url": args.api_url}


class Login(LoginInterface):
    """Login implementation that authenticates against channel app-specific passwords."""

    def __init__(self, config: Config, api_client: MessagesAPIClient) -> None:
        super().__init__()
        self.config: Final = config
        self.api_client: Final = api_client
        self._tokens = AllTokens(config)

    @property
    def tokens(self) -> AllTokens:
        return self._tokens

    async def authenticate(self, credentials: ServerCredentials) -> Identity:
        authcid = credentials.authcid

        if isinstance(credentials, TokenCredentials):
            raise InvalidAuth("Token authentication not supported.")

        # Extract the cleartext password from the credentials
        if isinstance(credentials, PlainCredentials):
            password = credentials._secret  # noqa: SLF001
        else:
            raise InvalidAuth("Only PLAIN/LOGIN authentication is supported.")

        if not password:
            raise InvalidAuth()

        # Authenticate via the Messages API
        channel_data = await self.api_client.authenticate_channel(authcid, password)
        if channel_data is None:
            raise InvalidAuth()

        # Reject channels without read access (e.g. sender_only)
        role = channel_data.get("role", "sender")
        if role == "sender_only":
            logger.warning("IMAP auth rejected for %s: sender_only has no read access", authcid)
            raise InvalidAuth()

        logger.info("Authenticated channel %s (role=%s)", authcid, role)
        return Identity(
            name=authcid,
            login=self,
            channel_data=channel_data,
            token=channel_data.get("token", ""),
        )

    async def authorize(self, authenticated: IdentityInterface, authzid: str) -> Identity:
        # An empty authzid means "authorize as self" (RFC 4616 §2).
        if authzid and authenticated.name != authzid:
            raise InvalidAuth("Authorization as a different user is not supported.")
        if not isinstance(authenticated, Identity):
            raise InvalidAuth()
        return authenticated


class Identity(IdentityInterface):
    """Identity representing an authenticated channel."""

    def __init__(self, name: str, login: Login, channel_data: dict, token: str = "") -> None:
        super().__init__()
        self._name = name
        self._login = login
        self._channel_data = channel_data
        self._token = token

    @property
    def name(self) -> str:
        return self._name

    @property
    def roles(self) -> frozenset[str]:
        return frozenset()

    @property
    def role(self) -> str:
        return self._channel_data.get("role", "sender")

    @property
    def token(self) -> str:
        return self._token

    @asynccontextmanager
    async def new_session(self) -> AsyncIterator[Session]:
        mailbox_id = self._channel_data["mailbox_id"]
        api_client = self._login.api_client.with_token(self._token)
        try:
            mailbox_set = MailboxSet(api_client, mailbox_id, role=self.role)
            yield Session(self._name, self._login.config, mailbox_set)
        finally:
            await api_client.close()

    async def new_token(self, *, expiration: datetime | None = None) -> str | None:
        return None

    async def get(self) -> UserMetadata:
        return UserMetadata(self._login.config, self._name)

    async def set(self, metadata: UserMetadata) -> int | None:
        raise UserNotFound()

    async def delete(self) -> None:
        raise UserNotFound()

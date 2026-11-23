"""Which MDA a recipient belongs to: one MTA in front of several Messages instances.

Without ``PYMTA_TENANTS_FILE`` there is one MDA, ``MDA_API_BASE_URL``, and
every recipient goes to it.

With it, the file maps each instance (a "tenant") to its MDA and the mail
domains it serves, as one JSON object::

    {
      "customer-a": {
        "base_url": "http://backend.customer-a.svc:8000/api/v1.0/",
        "secret": "<that instance's MDA_API_SECRET>",
        "domains": ["customer-a.example", "lists.customer-a.example"]
      }
    }

Each tenant gets its own ``MDAClient``: its own secret, so no instance can
sign calls to another, and its own breaker, so one instance down does not
shed the mail of the others.

The file is read again when it changes, at most once per
``PYMTA_TENANTS_RELOAD_INTERVAL``: a Kubernetes Secret mounted as a volume is
updated in place. A file that no longer parses is logged and the previous
mapping is kept.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import idna

from . import settings
from .mda_async import MDAClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Route:
    """Where a recipient is checked and delivered.

    ``tenant`` is empty with a single MDA.
    """

    tenant: str
    mda: MDAClient


@dataclass(frozen=True)
class _TenantConfig:
    base_url: str
    secret: str
    domains: tuple[str, ...]


def domain_key(domain: str) -> str:
    """A domain as the mapping is keyed: lower case, A-labels, no trailing dot.

    With SMTPUTF8 a recipient can name a domain in U-labels while the file
    lists it in A-labels, or the reverse. Anything IDNA refuses, an address
    literal for instance, is kept as it is and matches no tenant.
    """
    domain = domain.strip().rstrip(".").lower()
    try:
        return idna.encode(domain, uts46=True).decode("ascii")
    except UnicodeError:
        return domain


def _parse(raw: bytes) -> dict[str, _TenantConfig]:
    """The tenants file, checked. Raises ``ValueError`` naming what is wrong."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"tenants file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("tenants file must be a JSON object of tenants")

    tenants: dict[str, _TenantConfig] = {}
    owner: dict[str, str] = {}
    for name, entry in data.items():
        if not name or not isinstance(entry, dict):
            raise ValueError(f"tenant {name!r} must be a non-empty name mapped to an object")
        base_url, secret, domains = (
            entry.get("base_url"),
            entry.get("secret"),
            entry.get("domains"),
        )
        if not isinstance(base_url, str) or not base_url:
            raise ValueError(f"tenant {name!r} has no base_url")
        if not isinstance(secret, str) or not secret:
            raise ValueError(f"tenant {name!r} has no secret")
        if not isinstance(domains, list) or not all(isinstance(d, str) and d for d in domains):
            raise ValueError(f"tenant {name!r} must list its domains as non-empty strings")
        keys = []
        for domain in domains:
            key = domain_key(domain)
            if key in owner:
                raise ValueError(f"domain {key!r} is claimed by both {owner[key]!r} and {name!r}")
            owner[key] = name
            keys.append(key)
        tenants[name] = _TenantConfig(base_url=base_url, secret=secret, domains=tuple(keys))
    return tenants


class SingleTenant:
    """Every recipient goes to the one MDA: the deployment without a tenants file."""

    def __init__(self, mda: MDAClient):
        self._route = Route(tenant="", mda=mda)

    def route(self, address: str) -> Route:
        return self._route

    def breaker_is_open(self) -> bool:
        return self._route.mda.breaker_is_open()

    def clients(self) -> list[tuple[str, MDAClient]]:
        return []

    async def start(self) -> None:
        await self._route.mda.start()

    async def close(self) -> None:
        await self._route.mda.close()


class TenantRouter:
    """Routes a recipient to its tenant's MDA by the domain of its address."""

    def __init__(
        self,
        path: str,
        *,
        reload_interval: float | None = None,
        client_factory: Callable[..., MDAClient] = MDAClient,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.path = path
        self._reload_interval = (
            reload_interval
            if reload_interval is not None
            else settings.PYMTA_TENANTS_RELOAD_INTERVAL
        )
        self._client_factory = client_factory
        self._clock = clock
        # Per tenant: the (base_url, secret) its client was built with, and the client.
        self._clients: dict[str, tuple[tuple[str, str], MDAClient]] = {}
        # Clients of tenants removed or changed by a reload. A call may still be
        # in flight on one, so they are closed at shutdown, not at the reload.
        self._retired: list[MDAClient] = []
        self._routes: dict[str, Route] = {}
        self._file_id: tuple[int, int, int] | None = None
        self._next_check = 0.0
        # At startup a file that cannot be read or parsed is fatal.
        self._load(self._stat())

    def _stat(self) -> tuple[int, int, int]:
        st = os.stat(self.path)
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _load(self, file_id: tuple[int, int, int]) -> None:
        with open(self.path, "rb") as fh:
            tenants = _parse(fh.read())

        clients: dict[str, tuple[tuple[str, str], MDAClient]] = {}
        for name, config in tenants.items():
            key = (config.base_url, config.secret)
            current = self._clients.get(name)
            if current is not None and current[0] == key:
                # Unchanged: keep its connections and its breaker.
                clients[name] = current
            else:
                clients[name] = (
                    key,
                    self._client_factory(
                        base_url=config.base_url, secret=config.secret, tenant=name
                    ),
                )
        for name, (key, client) in self._clients.items():
            if name not in clients or clients[name][0] != key:
                self._retired.append(client)

        self._clients = clients
        self._routes = {
            domain: Route(tenant=name, mda=clients[name][1])
            for name, config in tenants.items()
            for domain in config.domains
        }
        self._file_id = file_id
        logger.info(
            "tenants_loaded",
            extra={"path": self.path, "tenants": len(tenants), "domains": len(self._routes)},
        )

    def _maybe_reload(self) -> None:
        now = self._clock()
        if now < self._next_check:
            return
        self._next_check = now + self._reload_interval
        try:
            file_id = self._stat()
            if file_id != self._file_id:
                self._load(file_id)
        except (OSError, ValueError) as exc:
            # Mid-update, or a bad edit: keep routing with what we have.
            logger.error(
                "tenants_reload_failed",
                extra={
                    "path": self.path,
                    "error": str(exc),
                    "detail": "keeping the previous mapping",
                },
            )

    def route(self, address: str) -> Route | None:
        """The tenant serving the domain of ``address``, or None if none does."""
        self._maybe_reload()
        return self._routes.get(domain_key(address.rpartition("@")[2]))

    def breaker_is_open(self) -> bool:
        """Whether any tenant's MDA is being shed, for the metrics gauge."""
        return any(client.breaker_is_open() for _, client in self._clients.values())

    def clients(self) -> list[tuple[str, MDAClient]]:
        return [(name, client) for name, (_, client) in self._clients.items()]

    async def start(self) -> None:
        """Nothing to open: each tenant's client connects on its first call."""

    async def close(self) -> None:
        for _, client in self._clients.values():
            await client.close()
        for client in self._retired:
            await client.close()
        self._retired.clear()


def build_router() -> SingleTenant | TenantRouter:
    """The router the settings ask for."""
    if settings.PYMTA_TENANTS_FILE:
        return TenantRouter(settings.PYMTA_TENANTS_FILE)
    return SingleTenant(MDAClient())

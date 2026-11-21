"""Abstract base class for sender trust backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import Mailbox


class SenderTrust(StrEnum):
    """How much a mailbox trusts an inbound sender.

    An enum rather than a bool so a backend can grow new levels (e.g. a
    sender the user blocked) without changing the interface.
    """

    #: No relationship known: the message goes through the normal spam check.
    UNKNOWN = "unknown"
    #: Known correspondent: the message is kept out of the Junk folder.
    TRUSTED = "trusted"


class SenderTrustUnavailableError(Exception):
    """The backend could not answer (e.g. a remote service is unreachable)."""


class SenderTrustBackend(ABC):
    """Interface every sender trust backend implements."""

    @abstractmethod
    def get_sender_trust(self, mailbox: Mailbox, sender_email: str) -> SenderTrust:
        """Return how much ``mailbox`` trusts ``sender_email``.

        Args:
            mailbox: The mailbox the message is delivered to.
            sender_email: The authenticated From address, already normalized
                with ``core.mda.addresses.normalize_address``.

        Raises:
            SenderTrustUnavailableError: If the backend cannot answer. The
                caller then treats the sender as ``UNKNOWN``.
        """

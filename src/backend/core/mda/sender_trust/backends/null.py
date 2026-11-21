"""Sender trust backend that trusts nobody, to turn the feature off."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.mda.sender_trust.backends.base import SenderTrust, SenderTrustBackend

if TYPE_CHECKING:
    from core.models import Mailbox


class NullSenderTrustBackend(SenderTrustBackend):
    """Always answer ``UNKNOWN``: every message gets the normal spam check."""

    def get_sender_trust(self, mailbox: Mailbox, sender_email: str) -> SenderTrust:
        return SenderTrust.UNKNOWN

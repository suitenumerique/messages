"""Local sender trust backend, computed from the mailbox's own sent messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import models
from core.mda.sender_trust.backends.base import SenderTrust, SenderTrustBackend

if TYPE_CHECKING:
    from core.models import Mailbox


class LocalSenderTrustBackend(SenderTrustBackend):
    """Trust a sender the mailbox has already written to.

    A sent message (not a draft) with the sender among its To/Cc/Bcc
    recipients is the evidence: a spammer can make us *receive* anything,
    but not make the mailbox write to them. A ``Contact`` alone proves
    nothing, since one is created for every inbound sender.

    Recipient contacts belong to the mailbox that sent the message, so
    ``contact__mailbox`` scopes the lookup to what this mailbox sent.

    Writing back to a spammer (an "unsubscribe" or "stop" reply, an answer
    to a phishing mail) is no relationship: a sent message flagged as spam,
    or sitting in a conversation that still holds spam, is ignored. Flagging
    the conversation as spam therefore withdraws the trust it gave.
    """

    def get_sender_trust(self, mailbox: Mailbox, sender_email: str) -> SenderTrust:
        # ``Contact.email`` keeps the casing it was typed with.
        has_written_to = models.MessageRecipient.objects.filter(
            contact__mailbox=mailbox,
            contact__email__iexact=sender_email,
            message__is_sender=True,
            message__is_draft=False,
            message__is_spam=False,
            message__thread__is_spam=False,
        ).exists()
        return SenderTrust.TRUSTED if has_written_to else SenderTrust.UNKNOWN

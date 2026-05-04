# pylint: disable=redefined-outer-name
"""Tests covering the spoofed-sender inbound bug.

When an inbound MTA email arrives with ``From == To`` (a classic header
forgery), the message must NOT be treated as a self-sent message. Marking it
``is_sender=True`` makes it match the ``retry_messages_task`` filter
(``is_sender=True`` AND ``delivery_status IN (RETRY, NULL)``), which would
re-emit the spam — externally signed with our DKIM key if any external CC is
present.

Legitimate self-sends are protected by the ``mime_id`` dedup in
``deliver_inbound_message``: the outbound copy already lives in the thread
when ``send_message`` triggers the internal redelivery, so no second copy
ever reaches ``_create_message_from_inbound``. The ``sender_email ==
recipient_email`` shortcut is therefore dead code for the legitimate path
and only fires for spoofed mails.
"""

from unittest.mock import patch

from django.utils import timezone

import pytest

from core import enums, factories, models
from core.mda.inbound import deliver_inbound_message
from core.mda.inbound_tasks import process_inbound_message_task
from core.mda.outbound_tasks import retry_messages_task


def _build_spoofed_raw(victim_email: str) -> bytes:
    """Build a minimal RFC 5322 message with From == To = victim."""
    return (
        f"From: {victim_email}\r\n"
        f"To: {victim_email}\r\n"
        "Subject: Spoofed self-sender\r\n"
        "Message-ID: <spoof.1@example.com>\r\n"
        "Date: Mon, 04 May 2026 09:00:00 +0000\r\n"
        "\r\n"
        "Spam body.\r\n"
    ).encode()


@pytest.fixture
def victim_mailbox():
    """Mailbox that receives the spoofed inbound."""
    domain = factories.MailDomainFactory(name="victim.test")
    return factories.MailboxFactory(local_part="alice", domain=domain)


@pytest.mark.django_db
class TestInboundSpoofedSender:
    """Inbound delivery with ``From == To`` must not flag ``is_sender=True``."""

    @patch("core.mda.inbound_tasks._check_spam_with_rspamd")
    def test_inbound_spoofed_sender_not_marked_as_sender(
        self, mock_rspamd, victim_mailbox
    ):
        """An MTA inbound spoofing From=To must NOT yield is_sender=True.

        Reproduces the production incident: rspamd lets the message through
        (``action: no action``), and our pipeline upgrades it to a self-sent
        message because of the ``sender_email == recipient_email`` shortcut.
        """
        # Rspamd doesn't catch this kind of spoof in the current config.
        mock_rspamd.return_value = (False, None, None)

        recipient = str(victim_mailbox)
        raw = _build_spoofed_raw(recipient)
        parsed_email = {
            "subject": "Spoofed self-sender",
            "from": {"email": recipient},
            "to": [{"email": recipient}],
            "messageId": "spoof.1@example.com",
            "date": timezone.now(),
            "headers": {},
            "headers_blocks": [],
        }

        # Bypass the Celery layer: deliver and process in-thread.
        with patch("core.mda.inbound.process_inbound_message_task.delay") as mock_delay:
            assert deliver_inbound_message(recipient, parsed_email, raw) is True

        inbound = models.InboundMessage.objects.get(mailbox=victim_mailbox)
        mock_delay.assert_called_once_with(str(inbound.id))

        process_inbound_message_task.apply(args=[str(inbound.id)])

        message = models.Message.objects.get(thread__accesses__mailbox=victim_mailbox)
        assert message.is_sender is False, (
            "Inbound MTA messages with spoofed From=To must not be flagged "
            "as is_sender=True; otherwise retry_messages_task re-emits them."
        )

    @patch("core.mda.inbound_tasks._check_spam_with_rspamd")
    @patch("core.mda.outbound_tasks.send_message")
    def test_inbound_spoofed_sender_not_picked_up_by_retry(
        self, mock_send_message, mock_rspamd, victim_mailbox
    ):
        """The retry task must skip messages built from a spoofed inbound.

        This is the actionable harm: even if ``is_sender`` somehow leaks True,
        the retry pipeline must not invoke ``send_message`` on it, because
        that would DKIM-sign and re-emit the spam.
        """
        mock_rspamd.return_value = (False, None, None)

        recipient = str(victim_mailbox)
        raw = _build_spoofed_raw(recipient)
        parsed_email = {
            "subject": "Spoofed self-sender",
            "from": {"email": recipient},
            "to": [{"email": recipient}],
            "messageId": "spoof.2@example.com",
            "date": timezone.now(),
            "headers": {},
            "headers_blocks": [],
        }

        with patch("core.mda.inbound.process_inbound_message_task.delay"):
            deliver_inbound_message(recipient, parsed_email, raw)

        inbound = models.InboundMessage.objects.get(mailbox=victim_mailbox)
        process_inbound_message_task.apply(args=[str(inbound.id)])

        # Sanity: a recipient row exists with NULL delivery_status (the
        # natural state for an inbound MessageRecipient).
        message = models.Message.objects.get(thread__accesses__mailbox=victim_mailbox)
        assert message.recipients.filter(delivery_status__isnull=True).exists()

        # Run the periodic retry task. It must not pick the spoofed message.
        result = retry_messages_task.apply(args=[]).get()

        assert result["total_messages"] == 0, (
            "retry_messages_task must not match an inbound spoofed message; "
            f"got total_messages={result['total_messages']}"
        )
        mock_send_message.assert_not_called()

    def test_inbound_spoofed_sender_legit_self_send_dedupes(self, victim_mailbox):
        """A genuine self-send is dedupped, not re-created as is_sender=True.

        The outbound message already lives in the thread before send_message
        triggers the internal inbound redelivery; the mime_id dedup in
        ``deliver_inbound_message`` short-circuits that redelivery so no
        second copy gets minted. This is what protects legitimate
        self-sends, NOT the ``sender_email == recipient_email`` shortcut in
        ``_create_message_from_inbound`` — which only ever fires for
        spoofed/anomalous mails and is the source of the bug.
        """
        recipient = str(victim_mailbox)
        mime_id = "self.legit.1@example.com"

        # Simulate the outbound message persisted by prepare_outbound_message.
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=victim_mailbox,
            thread=thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        outbound = factories.MessageFactory(
            thread=thread,
            mime_id=mime_id,
            is_draft=False,
            is_sender=True,
            subject="Genuine self-send",
        )

        parsed_email = {
            "subject": "Genuine self-send",
            "from": {"email": recipient},
            "to": [{"email": recipient}],
            "messageId": mime_id,
            "date": timezone.now(),
            "headers": {},
            "headers_blocks": [],
        }

        assert (
            deliver_inbound_message(
                recipient, parsed_email, b"raw", skip_inbound_queue=True
            )
            is True
        )

        # Dedup short-circuits: only the original outbound message exists.
        messages = models.Message.objects.filter(
            thread__accesses__mailbox=victim_mailbox
        )
        assert messages.count() == 1
        assert messages.first().id == outbound.id

    def test_inbound_spoofed_sender_import_path_never_picked_up_by_retry(
        self, victim_mailbox
    ):
        """An imported From=To message must not be picked up by retry.

        Imports legitimately mint is_sender=True (via the EML/mbox/IMAP/PST
        is_import_sender heuristic). The protection against the retry path
        is structural: imported recipients always carry
        ``delivery_status=SENT``, so the retry filter
        ``delivery_status IN (RETRY, NULL)`` excludes them by construction.
        """
        recipient = str(victim_mailbox)
        raw = _build_spoofed_raw(recipient)
        parsed_email = {
            "subject": "Imported self-send",
            "from": {"email": recipient},
            "to": [{"email": recipient}],
            "messageId": "import.self.1@example.com",
            "date": timezone.now(),
            "headers": {},
            "headers_blocks": [],
        }

        assert (
            deliver_inbound_message(
                recipient,
                parsed_email,
                raw,
                is_import=True,
                is_import_sender=True,
            )
            is True
        )

        message = models.Message.objects.get(thread__accesses__mailbox=victim_mailbox)
        assert message.is_sender is True
        # Every recipient row carries delivery_status=SENT, the structural
        # guarantee that imports never enter the retry pipeline.
        assert (
            message.recipients.filter(
                delivery_status=enums.MessageDeliveryStatusChoices.SENT
            ).count()
            == message.recipients.count()
        )

        result = retry_messages_task.apply(args=[]).get()
        assert result["total_messages"] == 0

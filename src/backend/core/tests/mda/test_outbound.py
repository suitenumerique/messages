"""Tests for the core.mda.outbound module."""

from unittest.mock import MagicMock, patch

from django.test import override_settings

import pytest

from core import enums, factories, models
from core.mda import outbound


@pytest.mark.django_db
class TestSendOutboundMessage:
    """Unit tests for the send_outbound_message function."""

    @pytest.fixture
    def draft_message(self):
        """Create a valid draft message with sender and recipients."""
        sender_contact = factories.ContactFactory(email="sender@sendtest.com")
        mailbox = sender_contact.mailbox
        thread = factories.ThreadFactory(mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender_contact,
            is_draft=False,
            subject="Test Outbound",
            raw_mime=b"From: sender@sendtest.com\nTo: to@example.com\nSubject: Test Outbound\n\nTest body",
        )
        # Add recipients
        to_contact = factories.ContactFactory(mailbox=mailbox, email="to@example.com")
        cc_contact = factories.ContactFactory(mailbox=mailbox, email="cc@example.com")
        bcc_contact = factories.ContactFactory(mailbox=mailbox, email="bcc@example.com")
        factories.MessageRecipientFactory(
            message=message,
            contact=to_contact,
            type=models.MessageRecipientTypeChoices.TO,
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=cc_contact,
            type=models.MessageRecipientTypeChoices.CC,
        )
        factories.MessageRecipientFactory(
            message=message,
            contact=bcc_contact,
            type=models.MessageRecipientTypeChoices.BCC,
        )
        return message

    @patch("core.mda.outbound.smtplib.SMTP")  # Mock SMTP client
    @override_settings(
        MTA_OUT_HOST="smtp.test:1025",
        MTA_OUT_SMTP_USE_TLS=False,  # Explicitly override TLS setting
        # Ensure other auth settings are None for this test
        MTA_OUT_SMTP_USERNAME=None,
        MTA_OUT_SMTP_PASSWORD=None,
    )
    def test_send_success(self, mock_smtp, draft_message):
        """Test successful sending: calls composer, dkim, smtp and updates message."""
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = (
            mock_smtp_instance  # Mock context manager
        )

        outbound.send_message(draft_message, force_mta_out=True)

        # Check SMTP calls
        mock_smtp.assert_called_once_with("smtp.test", 1025, timeout=10)
        mock_smtp_instance.ehlo.assert_called()
        # Assume no TLS/auth configured in this test override
        mock_smtp_instance.starttls.assert_not_called()
        mock_smtp_instance.login.assert_not_called()
        mock_smtp_instance.sendmail.assert_called_once()
        # Verify sendmail arguments
        call_args, _ = mock_smtp_instance.sendmail.call_args
        assert call_args[0] == draft_message.sender.email  # envelope_from
        assert set(call_args[1]) == {
            "to@example.com",
            "cc@example.com",
            "bcc@example.com",
        }  # envelope_to
        # Check that the signed message was sent
        assert call_args[2].endswith(draft_message.raw_mime)

        # Check message object updated
        draft_message.refresh_from_db()
        assert not draft_message.is_draft
        assert draft_message.sent_at is not None

        assert draft_message.recipients.count() == 3
        assert (
            draft_message.recipients.filter(
                delivery_status=enums.MessageDeliveryStatusChoices.SENT
            ).count()
            == 3
        )

"""Outbound correctness that the recipient's mail server can see."""

from unittest.mock import patch

import pytest
from rest_framework import exceptions as drf_exceptions

from core import enums, factories, models
from core.mda import outbound
from core.mda.outbound import (
    enforce_sender_domain_alignment,
    sender_domain_is_aligned,
)
from core.mda.outbound_tasks import aggregate_delivery_status, send_message_task
from core.mda.signing import DKIMSigningError, sign_message_dkim

pytestmark = pytest.mark.django_db


def _recipient(message, status):
    return models.MessageRecipient.objects.create(
        message=message,
        contact=factories.ContactFactory(),
        type=enums.MessageRecipientTypeChoices.TO,
        delivery_status=status,
    )


class TestAggregateDeliveryStatus:
    """A partial failure must not surface as fully delivered."""

    def test_all_delivered_is_completed(self):
        """The happy path is unchanged."""
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.SENT_EXTERNAL)
        _recipient(message, enums.MessageDeliveryStatusChoices.SENT_INTERNAL)

        assert aggregate_delivery_status(message)["status"] == "completed"

    def test_mixed_is_partial(self):
        """One failure among successes is reported, not hidden."""
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.SENT_EXTERNAL)
        _recipient(message, enums.MessageDeliveryStatusChoices.FAILED)

        result = aggregate_delivery_status(message)
        assert result["status"] == "partial"
        assert result["counts"] == {
            "delivered": 1,
            "failed": 1,
            "cancelled": 0,
            "pending": 0,
        }

    def test_all_cancelled_is_cancelled(self):
        """A send the user called off is not a delivery failure."""
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.CANCELLED)

        assert aggregate_delivery_status(message)["status"] == "cancelled"

    def test_all_cancelled_is_not_reported_as_success(self):
        """Cancelled delivered nothing, so ``success`` is False.

        It is still distinguishable from a fault: only ``delivery_status``
        separates "the user called it off" from "we could not deliver it",
        which is the reason the aggregate keeps them apart.
        """
        message = factories.MessageFactory(is_draft=False, is_sender=True)
        _recipient(message, enums.MessageDeliveryStatusChoices.CANCELLED)

        with patch("core.mda.outbound_tasks.send_message"):
            result = send_message_task.apply(args=[str(message.id)]).get()

        assert result["success"] is False
        assert result["delivery_status"] == "cancelled"

    def test_summary_travels_in_the_task_return_value(self):
        """``update_state`` meta is overwritten on return, so it cannot be there.

        Celery's ``mark_as_done`` calls ``store_result`` with the return value
        the moment the task returns, replacing any meta the task stored — so a
        summary published via ``update_state`` never reaches the client.
        """
        message = factories.MessageFactory(is_draft=False, is_sender=True)
        _recipient(message, enums.MessageDeliveryStatusChoices.SENT_EXTERNAL)
        _recipient(message, enums.MessageDeliveryStatusChoices.FAILED)

        with patch("core.mda.outbound_tasks.send_message"):
            result = send_message_task.apply(args=[str(message.id)]).get()

        assert result["delivery_status"] == "partial"
        assert result["recipients"]["failed"] == 1

    def test_all_failed_is_failed(self):
        """Nothing delivered is not "completed"."""
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.FAILED)

        assert aggregate_delivery_status(message)["status"] == "failed"

    def test_awaiting_retry_is_pending(self):
        """A recipient queued for retry is neither delivered nor failed."""
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.RETRY)

        assert aggregate_delivery_status(message)["status"] == "pending"

    def test_delivered_plus_retry_is_pending_not_partial(self):
        """A greylisted recipient beside a delivered one is still in flight.

        The common shape is one internal colleague (delivered at once) and one
        external address whose MX greylists. Calling that "partial" shows the
        user "not to every recipient" for mail that lands minutes later.
        """
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.SENT_INTERNAL)
        _recipient(message, enums.MessageDeliveryStatusChoices.RETRY)

        result = aggregate_delivery_status(message)
        assert result["status"] == "pending"
        assert result["counts"] == {
            "delivered": 1,
            "failed": 0,
            "cancelled": 0,
            "pending": 1,
        }

    def test_delivered_plus_failed_plus_retry_is_partial(self):
        """Once something has actually failed, "partial" is the honest word."""
        message = factories.MessageFactory()
        _recipient(message, enums.MessageDeliveryStatusChoices.SENT_EXTERNAL)
        _recipient(message, enums.MessageDeliveryStatusChoices.FAILED)
        _recipient(message, enums.MessageDeliveryStatusChoices.RETRY)

        assert aggregate_delivery_status(message)["status"] == "partial"


class TestDKIMFailsClosed:
    """A domain with an active key must never send unsigned."""

    def test_signing_error_raises(self):
        """A signing exception stops the send instead of downgrading it."""
        domain = factories.MailDomainFactory()
        assert domain.get_active_dkim_key() is not None

        with patch("core.mda.signing.dkim_sign", side_effect=RuntimeError("boom")):
            with pytest.raises(DKIMSigningError):
                sign_message_dkim(b"From: a@b.test\r\n\r\nbody", domain)

    def test_no_active_key_still_returns_none(self):
        """A domain with no key legitimately sends unsigned."""
        domain = factories.MailDomainFactory()
        domain.dkim_keys.update(is_active=False)

        assert sign_message_dkim(b"From: a@b.test\r\n\r\nbody", domain) is None


class TestSenderDomainAlignment:
    """The From domain and the DKIM d= must be the same domain."""

    def test_foreign_from_domain_is_reported(self):
        """A From on another domain is flagged as DMARC-misaligned."""
        mailbox = factories.MailboxFactory()
        sender = factories.ContactFactory(mailbox=mailbox, email="a@elsewhere.test")
        message = factories.MessageFactory(sender=sender)

        assert sender_domain_is_aligned(message, mailbox) is False

    def test_same_domain_other_mailbox_is_aligned(self):
        """Two mailboxes on one domain align: d= is the domain, not the box.

        The natural-looking check — ``sender.mailbox_id == mailbox.id`` — calls
        this a mismatch, which is why it is not the check used.
        """
        domain = factories.MailDomainFactory()
        signing_mailbox = factories.MailboxFactory(domain=domain)
        other_mailbox = factories.MailboxFactory(domain=domain)
        sender = factories.ContactFactory(
            mailbox=other_mailbox,
            email=f"{other_mailbox.local_part}@{domain.name}",
        )
        message = factories.MessageFactory(sender=sender)

        assert sender_domain_is_aligned(message, signing_mailbox) is True


class TestSenderRealignedWithSendingMailbox:
    """Replying from another mailbox signs with that mailbox's key.

    The draft carries the contact of the mailbox it was composed in, while the
    sending mailbox is chosen at send time. Keeping the first mailbox's From
    and signing with the second one's key is misaligned by construction, so
    the sending mailbox wins: the message is from it.
    """

    @staticmethod
    def _draft_in(mailbox):
        sender = factories.ContactFactory(mailbox=mailbox, email=str(mailbox))
        return factories.MessageFactory(sender=sender, is_draft=True, is_sender=True)

    def test_from_is_rewritten_to_the_sending_mailbox(self):
        drafting_mailbox = factories.MailboxFactory()
        sending_mailbox = factories.MailboxFactory()
        message = self._draft_in(drafting_mailbox)

        outbound._realign_sender_with_sending_mailbox(message, sending_mailbox)

        message.refresh_from_db()
        assert message.sender.email == str(sending_mailbox)
        assert sender_domain_is_aligned(message, sending_mailbox)

    def test_same_domain_leaves_the_sender_alone(self):
        """Alignment is per domain, so a second mailbox on it needs no rewrite.

        Rewriting anyway would silently change the visible From of a reply
        sent from a shared mailbox on the same domain.
        """
        domain = factories.MailDomainFactory()
        drafting_mailbox = factories.MailboxFactory(domain=domain)
        sending_mailbox = factories.MailboxFactory(domain=domain)
        message = self._draft_in(drafting_mailbox)
        original_sender_id = message.sender_id

        outbound._realign_sender_with_sending_mailbox(message, sending_mailbox)

        message.refresh_from_db()
        assert message.sender_id == original_sender_id


class TestMisalignedSendIsRefused:
    """A raw MIME submission carrying a foreign From must not be signed.

    Nothing realigns there — the caller wrote the From — so the only options
    are refusing or sending a guaranteed DMARC failure.
    """

    @staticmethod
    def _misaligned():
        mailbox = factories.MailboxFactory()
        sender = factories.ContactFactory(mailbox=mailbox, email="a@elsewhere.test")
        return factories.MessageFactory(sender=sender), mailbox

    def test_refused_when_outgoing_dkim_is_verified(self, settings):
        settings.MESSAGES_DKIM_VERIFY_OUTGOING = True
        message, mailbox = self._misaligned()

        with pytest.raises(drf_exceptions.ValidationError) as excinfo:
            enforce_sender_domain_alignment(message, mailbox)

        assert "elsewhere.test" in str(excinfo.value)

    def test_only_logged_when_the_setting_is_off(self, settings):
        """Opt-in, like the rest of that switch: no new hard failure by default."""
        settings.MESSAGES_DKIM_VERIFY_OUTGOING = False
        message, mailbox = self._misaligned()

        enforce_sender_domain_alignment(message, mailbox)

    def test_aligned_send_is_never_refused(self, settings):
        settings.MESSAGES_DKIM_VERIFY_OUTGOING = True
        mailbox = factories.MailboxFactory()
        sender = factories.ContactFactory(mailbox=mailbox, email=str(mailbox))
        message = factories.MessageFactory(sender=sender)

        enforce_sender_domain_alignment(message, mailbox)

"""Regression tests: recipient rows vanishing while the worker is on the wire.

A draft-update racing the send used to rewrite the recipients
(delete + get_or_create → new UUIDs) while the outbound worker held the old
rows, making the post-SMTP status save crash the whole delivery with
``DatabaseError: Save with update_fields did not affect any rows.`` (or a
``validate_unique`` collision once the rewrite had fully committed).

Fixes under test:
* ``update_draft`` refuses messages that are no longer drafts;
* the worker tolerates a vanished recipient row (warning, not crash) while
  still firing the post_save signals — thread stats, search reindex — on
  the nominal path;
* the SMTP-failure fallback never flips an already-recorded recipient back
  to RETRY (which would double-send);
* only the vanished-row case is absorbed: a status save failing while the
  row still exists (deadlock, timeout) surfaces as an error instead of
  being mislabeled "vanished" — the misleading label hid writes whose loss
  makes the retry task deliver the same email twice.
"""

from unittest.mock import patch

from django.db import DatabaseError

import pytest
import rest_framework as drf

from core import enums, factories, models
from core.mda import outbound
from core.mda.draft import update_draft

pytestmark = pytest.mark.django_db


@patch("core.mda.outbound.send_smtp_mail")
def test_recipient_deleted_during_smtp_does_not_crash(
    mock_smtp_send, sendable_message, relay_settings
):
    """A recipient deleted mid-SMTP is logged, not fatal."""
    message = sendable_message
    original_recipient = message.recipients.get()

    def delete_lands_mid_smtp(*args, **kwargs):
        models.MessageRecipient.objects.filter(pk=original_recipient.pk).delete()
        return {"to@example.com": {"delivered": True, "error": None}}

    mock_smtp_send.side_effect = delete_lands_mid_smtp

    with patch.object(outbound.logger, "warning") as mock_warning:
        outbound.send_message(message)

    assert any(
        "vanished during delivery" in str(call.args[0])
        for call in mock_warning.call_args_list
    )


@patch("core.mda.outbound.send_smtp_mail")
def test_recipient_recreated_during_smtp_does_not_crash(
    mock_smtp_send, sendable_message, relay_settings
):
    """A recipient rewrite (delete + recreate) mid-SMTP is logged, not fatal.

    Simulated with raw ORM calls: the API path can no longer do this
    (row lock in the draft PUT + ``is_draft`` guard in ``update_draft``).
    """
    message = sendable_message
    original_recipient = message.recipients.get()

    def rewrite_lands_mid_smtp(*args, **kwargs):
        contact = original_recipient.contact
        models.MessageRecipient.objects.filter(pk=original_recipient.pk).delete()
        models.MessageRecipient.objects.create(
            message=message,
            contact=contact,
            type=models.MessageRecipientTypeChoices.TO,
        )
        return {"to@example.com": {"delivered": True, "error": None}}

    mock_smtp_send.side_effect = rewrite_lands_mid_smtp

    with patch.object(outbound.logger, "warning") as mock_warning:
        outbound.send_message(message)

    assert any(
        "vanished during delivery" in str(call.args[0])
        for call in mock_warning.call_args_list
    )
    recreated = models.MessageRecipient.objects.get(message=message)
    assert recreated.id != original_recipient.id


def test_update_draft_refuses_finalized_message(sendable_message):
    """The recipient-rewrite invariant is structural, not just in views."""
    message = sendable_message

    with pytest.raises(drf.exceptions.ValidationError, match="no longer a draft"):
        update_draft(message.sender.mailbox, message, {"to": ["to@example.com"]})


@patch("core.mda.outbound.send_smtp_mail")
def test_delivery_status_save_still_updates_thread_stats(
    mock_smtp_send, sendable_message, relay_settings
):
    """The nominal status save must keep firing post_save side effects.

    ``has_delivery_pending`` is recomputed from a MessageRecipient
    post_save signal (batched by ThreadStatsUpdateDeferrer); replacing the
    save with a queryset UPDATE would leave every sent thread stuck in
    "pending" state.
    """
    message = sendable_message
    thread = message.thread
    thread.update_stats()
    assert thread.has_delivery_pending is True

    mock_smtp_send.return_value = {"to@example.com": {"delivered": True, "error": None}}

    outbound.send_message(message)

    thread.refresh_from_db()
    assert thread.has_delivery_pending is False


@patch("core.mda.outbound.send_smtp_mail")
def test_malformed_status_entry_does_not_flip_delivered_recipients(
    mock_smtp_send, sendable_message, relay_settings
):
    """One bad MTA status entry must not corrupt the other recipients.

    The malformed entry comes first so a naive sequential loop would abort
    before recording the delivered one, then re-mark it RETRY — and the
    retry task would send the same email a second time.
    """
    message = sendable_message
    mailbox = message.sender.mailbox
    cc_contact = factories.ContactFactory(mailbox=mailbox, email="cc@example.com")
    factories.MessageRecipientFactory(
        message=message,
        contact=cc_contact,
        type=models.MessageRecipientTypeChoices.CC,
    )

    mock_smtp_send.return_value = {
        "cc@example.com": None,  # malformed: crashes on subscript
        "to@example.com": {"delivered": True, "error": None},
    }

    outbound.send_message(message)

    delivered = message.recipients.get(contact__email="to@example.com")
    assert delivered.delivery_status == enums.MessageDeliveryStatusChoices.SENT_EXTERNAL
    # The malformed entry's recipient keeps its unset status: the retry
    # task will pick it up, which is the correct outcome for an unknown
    # delivery result.
    pending = message.recipients.get(contact__email="cc@example.com")
    assert pending.delivery_status is None


@patch("core.mda.outbound.send_smtp_mail")
def test_smtp_send_failure_marks_all_recipients_for_retry(
    mock_smtp_send, sendable_message, relay_settings
):
    """When the MTA reported nothing, every recipient goes to RETRY."""
    message = sendable_message
    mock_smtp_send.side_effect = ConnectionError("relay unreachable")

    outbound.send_message(message)

    recipient = message.recipients.get()
    assert recipient.delivery_status == enums.MessageDeliveryStatusChoices.RETRY


def test_save_recipient_status_reraises_when_row_still_exists(sendable_message):
    """A DB failure with the row still present must propagate, not be absorbed.

    Absorbing it as "vanished row" leaves ``delivery_status`` NULL: the
    retry task selects NULL statuses and re-enters ``send_message``, so a
    recipient the MTA already accepted would receive the email twice —
    with only a benign-looking warning in the logs.
    """
    recipient = sendable_message.recipients.get()
    recipient.delivery_status = enums.MessageDeliveryStatusChoices.SENT_EXTERNAL

    with (
        patch.object(
            models.MessageRecipient, "save", side_effect=DatabaseError("deadlock")
        ),
        pytest.raises(DatabaseError),
    ):
        outbound._save_recipient_status(recipient, ["delivery_status"])


@patch("core.mda.outbound.send_smtp_mail")
def test_transient_db_error_is_not_mistaken_for_vanished_row(
    mock_smtp_send, sendable_message, relay_settings
):
    """End to end, a deadlock on the status save surfaces as an error.

    The per-recipient handler in ``send_message`` keeps the delivery loop
    alive, but the failure must be logged as an error ("Failed to record
    delivery status"), never as the benign "vanished during delivery"
    warning.
    """
    message = sendable_message
    mock_smtp_send.return_value = {"to@example.com": {"delivered": True, "error": None}}

    with (
        patch.object(
            models.MessageRecipient, "save", side_effect=DatabaseError("deadlock")
        ),
        patch.object(outbound.logger, "exception") as mock_exception,
        patch.object(outbound.logger, "warning") as mock_warning,
    ):
        outbound.send_message(message)

    assert any(
        "Failed to record delivery status" in str(call.args[0])
        for call in mock_exception.call_args_list
    )
    assert not any(
        "vanished during delivery" in str(call.args[0])
        for call in mock_warning.call_args_list
    )

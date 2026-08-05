"""Deterministic inbound failures must not be re-dispatched for 48 hours.

A parse failure fails identically on every attempt, so the deferral window
buys nothing: abandon on the first one. The failures that *are* worth
retrying, and how long they're held, live in ``test_inbound_retry_backoff``.
"""
# ``process_inbound_message_task`` is a bound Celery task; calling it
# directly is how the other task tests drive it, and pylint cannot see
# that ``self`` is already bound.
# pylint: disable=unused-argument, no-value-for-parameter

from unittest.mock import patch

import pytest

from core import factories, models
from core.mda.inbound_tasks import process_inbound_message_task


def _inbound(mailbox, content=b"raw"):
    blob = factories.BlobFactory(
        mailbox=mailbox, content=content, content_type="message/rfc822"
    )
    return models.InboundMessage.objects.create(mailbox=mailbox, blob=blob)


@pytest.mark.django_db
class TestUnparseableIsAbandonedImmediately:
    """A parse failure is deterministic — abandon on the first attempt."""

    def test_parse_failure_stamps_abandoned_at(self):
        """The stamp is what stops the 5-min sweep re-dispatching it."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(mailbox)

        with patch("core.mda.inbound_tasks.parse_email", return_value=None):
            result = process_inbound_message_task(str(inbound.id))

        assert result["error"] == "abandoned"
        inbound.refresh_from_db()
        assert inbound.abandoned_at is not None, (
            "an unparseable message must be stamped terminally failed, not "
            "left live for the 5-min sweep to retry for 48h"
        )
        assert inbound.error_message == "Failed to parse email message"

    def test_abandoned_row_is_excluded_from_the_retry_sweep(self):
        """The stamp is what stops the loop — the sweep filters on
        ``abandoned_at__isnull=True``."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(mailbox)

        with patch("core.mda.inbound_tasks.parse_email", return_value=None):
            process_inbound_message_task(str(inbound.id))

        live = models.InboundMessage.objects.filter(abandoned_at__isnull=True)
        assert inbound.id not in {row.id for row in live}

    def test_the_blob_is_kept_for_replay(self):
        """Abandoning must never delete the row; the blob is the only
        copy of the mail."""
        mailbox = factories.MailboxFactory()
        inbound = _inbound(mailbox, content=b"the only copy")

        with patch("core.mda.inbound_tasks.parse_email", return_value=None):
            process_inbound_message_task(str(inbound.id))

        inbound.refresh_from_db()
        assert inbound.blob is not None
        assert inbound.blob.get_content() == b"the only copy"

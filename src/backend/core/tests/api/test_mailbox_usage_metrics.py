"""Tests for the mailbox usage metrics endpoint."""
# pylint: disable=redefined-outer-name

from django.urls import reverse

import pytest

from core.enums import MessageTemplateTypeChoices
from core.factories import (
    AttachmentFactory,
    BlobFactory,
    ContactFactory,
    MailboxFactory,
    MessageFactory,
    MessageTemplateFactory,
    ThreadAccessFactory,
    ThreadFactory,
)


@pytest.fixture
def url():
    """Returns the URL for the mailbox usage metrics endpoint."""
    return reverse("mailbox-usage-metrics")


@pytest.fixture
def correctly_configured_header(settings):
    """Returns the authentication header for the metrics endpoint."""
    return {"HTTP_AUTHORIZATION": f"Bearer {settings.METRICS_API_KEY}"}


class TestMailboxUsageMetrics:
    """Tests for the mailbox usage metrics endpoint."""

    @pytest.mark.django_db
    def test_requires_auth(self, api_client, url, correctly_configured_header):
        """Requires valid API key for access."""
        # Without authentication
        response = api_client.get(url)
        assert response.status_code == 403

        # Invalid authentication
        response = api_client.get(url, HTTP_AUTHORIZATION="Bearer invalid_token")
        assert response.status_code == 403

        # Valid authentication
        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_empty(self, api_client, url, correctly_configured_header):
        """Returns empty results when no mailboxes exist."""
        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200
        assert response.json() == {"count": 0, "results": []}

    @pytest.mark.django_db
    def test_mailbox_no_messages(self, api_client, url, correctly_configured_header):
        """A mailbox with no messages and no attachments has zero storage."""
        MailboxFactory(local_part="alice", domain__name="example.com")

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["email"] == "alice@example.com"
        assert data["results"][0]["storage_used"] == 0

    @pytest.mark.django_db
    def test_orphan_blob_not_counted(
        self, api_client, url, correctly_configured_header
    ):
        """A blob linked only via blob.mailbox (orphan upload) is not counted."""
        mailbox = MailboxFactory(local_part="alice", domain__name="example.com")
        BlobFactory(mailbox=mailbox, content=b"orphan" * 100)

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["storage_used"] == 0

    @pytest.mark.django_db
    def test_messages_count_overhead(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Messages without MIME blobs count only the per-message overhead."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox = MailboxFactory(local_part="bob", domain__name="test.org")
        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)
        MessageFactory(thread=thread, sender=contact)
        MessageFactory(thread=thread, sender=contact)

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["storage_used"] == 2 * overhead

    @pytest.mark.django_db
    def test_formula_with_mime_blobs_and_attachments(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Full formula: overhead + MIME blob sizes + attachment blob sizes."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox = MailboxFactory(local_part="bob", domain__name="test.org")
        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)

        # 2 messages with raw MIME blobs
        msg1 = MessageFactory(thread=thread, sender=contact, raw_mime=b"mime1" * 100)
        msg2 = MessageFactory(thread=thread, sender=contact, raw_mime=b"mime2" * 200)

        # 1 attachment on msg1
        att = AttachmentFactory(mailbox=mailbox, blob_size=500)
        att.messages.add(msg1)

        expected = (
            2 * overhead
            + msg1.blob.size_compressed
            + msg2.blob.size_compressed
            + att.blob.size_compressed
        )

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["email"] == "bob@test.org"
        assert data["results"][0]["storage_used"] == expected

    @pytest.mark.django_db
    def test_multiple_mailboxes(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Verifies independent storage computation across multiple mailboxes."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox_a = MailboxFactory(local_part="alice", domain__name="a.com")
        mailbox_b = MailboxFactory(local_part="bob", domain__name="b.com")

        # mailbox_a: 2 messages with MIME blobs
        thread_a = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=thread_a)
        contact_a = ContactFactory(mailbox=mailbox_a)
        msg_a1 = MessageFactory(thread=thread_a, sender=contact_a, raw_mime=b"a1" * 100)
        msg_a2 = MessageFactory(thread=thread_a, sender=contact_a, raw_mime=b"a2" * 100)

        # mailbox_b: 1 message + 1 attachment
        thread_b = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_b, thread=thread_b)
        contact_b = ContactFactory(mailbox=mailbox_b)
        msg_b = MessageFactory(thread=thread_b, sender=contact_b, raw_mime=b"b1" * 50)
        att_b = AttachmentFactory(mailbox=mailbox_b, blob_size=300)
        att_b.messages.add(msg_b)

        expected_a = (
            2 * overhead + msg_a1.blob.size_compressed + msg_a2.blob.size_compressed
        )
        expected_b = (
            1 * overhead + msg_b.blob.size_compressed + att_b.blob.size_compressed
        )

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 2
        results_by_email = {r["email"]: r for r in data["results"]}
        assert results_by_email["alice@a.com"]["storage_used"] == expected_a
        assert results_by_email["bob@b.com"]["storage_used"] == expected_b

    @pytest.mark.django_db
    def test_multiple_threads_same_mailbox(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Messages across multiple threads for the same mailbox are all counted."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox = MailboxFactory(local_part="eve", domain__name="test.com")
        contact = ContactFactory(mailbox=mailbox)

        thread1 = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread1)
        MessageFactory(thread=thread1, sender=contact)

        thread2 = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread2)
        MessageFactory(thread=thread2, sender=contact)
        MessageFactory(thread=thread2, sender=contact)

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["storage_used"] == 3 * overhead

    @pytest.mark.django_db
    def test_shared_thread_counts_for_all_mailboxes(
        self, api_client, url, correctly_configured_header, settings
    ):
        """A shared thread's messages and MIME blobs count toward every mailbox."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox_a = MailboxFactory(local_part="alice", domain__name="a.com")
        mailbox_b = MailboxFactory(local_part="bob", domain__name="b.com")

        # Shared thread: both mailboxes have access, messages have MIME blobs
        shared_thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=shared_thread)
        ThreadAccessFactory(mailbox=mailbox_b, thread=shared_thread)
        contact = ContactFactory(mailbox=mailbox_a)
        msg1 = MessageFactory(
            thread=shared_thread, sender=contact, raw_mime=b"shared1" * 100
        )
        msg2 = MessageFactory(
            thread=shared_thread, sender=contact, raw_mime=b"shared2" * 100
        )

        # Private thread: only mailbox_a
        private_thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=private_thread)
        msg3 = MessageFactory(
            thread=private_thread, sender=contact, raw_mime=b"private" * 50
        )

        shared_blob_size = msg1.blob.size_compressed + msg2.blob.size_compressed

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        results_by_email = {r["email"]: r for r in data["results"]}

        # mailbox_a: 3 messages + all 3 MIME blobs
        assert results_by_email["alice@a.com"]["storage_used"] == (
            3 * overhead + shared_blob_size + msg3.blob.size_compressed
        )
        # mailbox_b: 2 shared messages + 2 shared MIME blobs
        assert results_by_email["bob@b.com"]["storage_used"] == (
            2 * overhead + shared_blob_size
        )

    @pytest.mark.django_db
    def test_draft_with_attachments(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Draft attachments are counted via Attachment.mailbox."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox = MailboxFactory(local_part="carol", domain__name="test.com")
        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)

        # Draft message with a draft_blob body
        draft_blob = BlobFactory(mailbox=mailbox, content=b"draft body" * 50)
        msg = MessageFactory(
            thread=thread, sender=contact, is_draft=True, draft_blob=draft_blob
        )

        # Two attachments on the draft
        att1 = AttachmentFactory(mailbox=mailbox, blob_size=1000)
        att2 = AttachmentFactory(mailbox=mailbox, blob_size=2000)
        att1.messages.add(msg)
        att2.messages.add(msg)

        expected = (
            1 * overhead
            + draft_blob.size_compressed
            + att1.blob.size_compressed
            + att2.blob.size_compressed
        )

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["storage_used"] == expected

    @pytest.mark.django_db
    def test_blobs_with_identical_sizes_counted_separately(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Two different blobs that happen to have the same compressed size
        must each be counted toward storage, not collapsed into one."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox = MailboxFactory(local_part="alice", domain__name="test.com")
        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)

        same_content = b"x" * 500
        msg1 = MessageFactory(thread=thread, sender=contact, raw_mime=same_content)
        msg2 = MessageFactory(thread=thread, sender=contact, raw_mime=same_content)

        assert msg1.blob.size_compressed == msg2.blob.size_compressed
        assert msg1.blob.pk != msg2.blob.pk

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        expected = 2 * overhead + msg1.blob.size_compressed + msg2.blob.size_compressed
        assert data["results"][0]["storage_used"] == expected

    @pytest.mark.django_db
    def test_storage_includes_template_blobs(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Mailbox signature/template blobs are counted toward storage."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox = MailboxFactory(local_part="alice", domain__name="test.com")
        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)
        msg = MessageFactory(thread=thread, sender=contact, raw_mime=b"mime" * 100)

        # Mailbox-level signature template
        sig = MessageTemplateFactory(
            mailbox=mailbox,
            maildomain=None,
            type=MessageTemplateTypeChoices.SIGNATURE,
        )

        expected = 1 * overhead + msg.blob.size_compressed + sig.blob.size_compressed

        response = api_client.get(url, **correctly_configured_header)
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["storage_used"] == expected

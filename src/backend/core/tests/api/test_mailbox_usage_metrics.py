"""Tests for the mailbox usage metrics endpoint."""
# pylint: disable=redefined-outer-name, too-many-public-methods

from django.urls import reverse

import pytest

from core.enums import MessageTemplateTypeChoices
from core.factories import (
    AttachmentFactory,
    BlobFactory,
    ContactFactory,
    MailboxFactory,
    MailDomainFactory,
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


CUSTOM_ATTRIBUTES_SCHEMA = {
    "type": "object",
    "properties": {
        "org_id": {"type": "string"},
        "tenant": {"type": "string"},
        "region": {"type": "string"},
        "code": {"type": "string"},
    },
}


@pytest.mark.django_db
class TestMailboxUsageMetrics:
    """Tests for the mailbox usage metrics endpoint."""

    @pytest.fixture(autouse=True)
    def _set_custom_attributes_schema(self, settings):
        settings.SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN = CUSTOM_ATTRIBUTES_SCHEMA

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
        assert data["results"][0]["account"]["email"] == "alice@example.com"
        assert data["results"][0]["metrics"]["storage_used"] == 0

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
        assert data["results"][0]["metrics"]["storage_used"] == 0

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
        assert data["results"][0]["metrics"]["storage_used"] == 2 * overhead

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
        assert data["results"][0]["account"]["email"] == "bob@test.org"
        assert data["results"][0]["metrics"]["storage_used"] == expected

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
        results_by_email = {r["account"]["email"]: r for r in data["results"]}
        assert results_by_email["alice@a.com"]["metrics"]["storage_used"] == expected_a
        assert results_by_email["bob@b.com"]["metrics"]["storage_used"] == expected_b

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
        assert data["results"][0]["metrics"]["storage_used"] == 3 * overhead

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

        results_by_email = {r["account"]["email"]: r for r in data["results"]}

        # mailbox_a: 3 messages + all 3 MIME blobs
        assert results_by_email["alice@a.com"]["metrics"]["storage_used"] == (
            3 * overhead + shared_blob_size + msg3.blob.size_compressed
        )
        # mailbox_b: 2 shared messages + 2 shared MIME blobs
        assert results_by_email["bob@b.com"]["metrics"]["storage_used"] == (
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
        assert data["results"][0]["metrics"]["storage_used"] == expected

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
        assert data["results"][0]["metrics"]["storage_used"] == expected

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
        assert data["results"][0]["metrics"]["storage_used"] == expected

    @pytest.mark.django_db
    def test_filter_by_domain(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Filter results by domain name."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        mailbox_a = MailboxFactory(local_part="alice", domain__name="a.com")
        MailboxFactory(local_part="bob", domain__name="b.com")

        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=thread)
        contact = ContactFactory(mailbox=mailbox_a)
        MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url, {"domain": "a.com"}, **correctly_configured_header
        )
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["account"]["email"] == "alice@a.com"
        assert data["results"][0]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_filter_by_domain_no_match(
        self, api_client, url, correctly_configured_header
    ):
        """Domain filter with no matching domain returns empty results."""
        MailboxFactory(local_part="alice", domain__name="a.com")

        response = api_client.get(
            url, {"domain": "nonexistent.com"}, **correctly_configured_header
        )
        data = response.json()

        assert data["count"] == 0
        assert data["results"] == []

    @pytest.mark.django_db
    def test_filter_by_account_email(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Filter results by account_email."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain = MailDomainFactory(name="a.com")
        mailbox_a = MailboxFactory(local_part="alice", domain=domain)
        mailbox_b = MailboxFactory(local_part="bob", domain=domain)

        for mailbox in [mailbox_a, mailbox_b]:
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mailbox, thread=thread)
            contact = ContactFactory(mailbox=mailbox)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url, {"account_email": "alice@a.com"}, **correctly_configured_header
        )
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["account"]["email"] == "alice@a.com"
        assert data["results"][0]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_filter_by_account_email_no_match(
        self, api_client, url, correctly_configured_header
    ):
        """account_email filter with no matching email returns empty results."""
        MailboxFactory(local_part="alice", domain__name="a.com")

        response = api_client.get(
            url, {"account_email": "nobody@a.com"}, **correctly_configured_header
        )
        data = response.json()

        assert data["count"] == 0
        assert data["results"] == []

    @pytest.mark.django_db
    def test_filter_domain_and_email_combined(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Both domain and account_email filters can be combined."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com")
        mailbox = MailboxFactory(local_part="alice", domain=domain_a)
        MailboxFactory(local_part="bob", domain=domain_a)
        MailboxFactory(local_part="carol", domain__name="b.com")

        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)
        MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {"domain": "a.com", "account_email": "alice@a.com"},
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["account"]["email"] == "alice@a.com"
        assert data["results"][0]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_filter_by_custom_attribute(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Filter by account_id_key + account_id_value
        matches domains whose custom_attributes contain the given value."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"org_id": "111"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"org_id": "222"})

        mailbox_a = MailboxFactory(local_part="alice", domain=domain_a)
        mailbox_b = MailboxFactory(local_part="bob", domain=domain_b)

        for mailbox in [mailbox_a, mailbox_b]:
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mailbox, thread=thread)
            contact = ContactFactory(mailbox=mailbox)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {
                "account_id_key": "org_id",
                "account_id_value": "111",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["org_id"] == "111"
        assert data["results"][0]["account"]["email"] == "alice@a.com"
        assert data["results"][0]["account"]["type"] == "mailbox"
        assert data["results"][0]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_filter_by_custom_attribute_multiple_domains(
        self, api_client, url, correctly_configured_header
    ):
        """Multiple domains sharing the same custom attribute value
        return all their mailboxes."""
        domain_a = MailDomainFactory(name="a.com", custom_attributes={"org_id": "111"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"org_id": "111"})
        domain_c = MailDomainFactory(name="c.com", custom_attributes={"org_id": "999"})

        mailbox_a = MailboxFactory(local_part="alice", domain=domain_a)
        mailbox_b = MailboxFactory(local_part="bob", domain=domain_b)
        mailbox_c = MailboxFactory(local_part="carol", domain=domain_c)

        for mailbox in [mailbox_a, mailbox_b, mailbox_c]:
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mailbox, thread=thread)
            contact = ContactFactory(mailbox=mailbox)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {
                "account_id_key": "org_id",
                "account_id_value": "111",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 2
        emails = {r["account"]["email"] for r in data["results"]}
        assert emails == {"alice@a.com", "bob@b.com"}

    @pytest.mark.django_db
    def test_filter_by_custom_attribute_no_match(
        self, api_client, url, correctly_configured_header
    ):
        """Custom attribute filter with no match returns empty results."""
        MailDomainFactory(name="a.com", custom_attributes={"org_id": "111"})

        response = api_client.get(
            url,
            {
                "account_id_key": "org_id",
                "account_id_value": "999",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 0
        assert data["results"] == []

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "key",
        ["org_id__gte", "contains", "account", "metrics", "unknown"],
    )
    def test_invalid_account_id_key(
        self, api_client, url, correctly_configured_header, key
    ):
        """account_id_key not declared in SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN
        is rejected."""
        response = api_client.get(
            url,
            {"account_id_key": key, "account_id_value": "111"},
            **correctly_configured_header,
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_account_id_key_only_includes_attribute(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Providing account_id_key without account_id_value returns the full
        list with the custom attribute included in each result."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"org_id": "111"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"org_id": "222"})

        mailbox_a = MailboxFactory(local_part="alice", domain=domain_a)
        mailbox_b = MailboxFactory(local_part="bob", domain=domain_b)

        for mailbox in [mailbox_a, mailbox_b]:
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mailbox, thread=thread)
            contact = ContactFactory(mailbox=mailbox)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {"account_id_key": "org_id"},
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 2
        results_by_email = {r["account"]["email"]: r for r in data["results"]}
        assert results_by_email["alice@a.com"]["org_id"] == "111"
        assert (
            results_by_email["alice@a.com"]["metrics"]["storage_used"] == 1 * overhead
        )
        assert results_by_email["bob@b.com"]["org_id"] == "222"
        assert results_by_email["bob@b.com"]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_organization_aggregates_storage(
        self, api_client, url, correctly_configured_header, settings
    ):
        """account_type=organization aggregates storage across all matching
        mailboxes into a single result."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"org_id": "111"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"org_id": "111"})
        MailDomainFactory(name="c.com", custom_attributes={"org_id": "999"})

        mailbox_a = MailboxFactory(local_part="alice", domain=domain_a)
        mailbox_b = MailboxFactory(local_part="bob", domain=domain_b)

        for mailbox in [mailbox_a, mailbox_b]:
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mailbox, thread=thread)
            contact = ContactFactory(mailbox=mailbox)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {
                "account_type": "organization",
                "account_id_value": "111",
                "account_id_key": "org_id",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        result = data["results"][0]
        assert result["org_id"] == "111"
        assert result["account"] == {"type": "organization"}
        assert result["metrics"]["storage_used"] == 2 * overhead

    @pytest.mark.django_db
    def test_organization_requires_custom_attribute_filter(
        self, api_client, url, correctly_configured_header
    ):
        """account_type=organization without account_id_key
        and account_id_value returns 400."""
        # Missing account_id_key
        response = api_client.get(
            url,
            {"account_type": "organization", "account_id_value": "111"},
            **correctly_configured_header,
        )
        assert response.status_code == 400

        # Missing account_id_value
        response = api_client.get(
            url,
            {
                "account_type": "organization",
                "account_id_key": "org_id",
            },
            **correctly_configured_header,
        )
        assert response.status_code == 400

        # Missing both
        response = api_client.get(
            url,
            {"account_type": "organization"},
            **correctly_configured_header,
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_organization_no_matching_mailboxes(
        self, api_client, url, correctly_configured_header
    ):
        """account_type=organization with no matching mailboxes returns empty."""
        MailDomainFactory(name="a.com", custom_attributes={"org_id": "111"})

        response = api_client.get(
            url,
            {
                "account_type": "organization",
                "account_id_value": "999",
                "account_id_key": "org_id",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 0
        assert data["results"] == []

    @pytest.mark.django_db
    def test_maildomain_aggregates_per_domain(
        self, api_client, url, correctly_configured_header, settings
    ):
        """account_type=maildomain returns one result per domain with
        aggregated storage across its mailboxes."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"tenant": "t1"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"tenant": "t1"})

        # 2 mailboxes on domain_a, 1 on domain_b
        mb_a1 = MailboxFactory(local_part="alice", domain=domain_a)
        mb_a2 = MailboxFactory(local_part="ann", domain=domain_a)
        mb_b = MailboxFactory(local_part="bob", domain=domain_b)

        for mailbox in [mb_a1, mb_a2, mb_b]:
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mailbox, thread=thread)
            contact = ContactFactory(mailbox=mailbox)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {
                "account_type": "maildomain",
                "account_id_value": "t1",
                "account_id_key": "tenant",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 2
        by_id = {r["account"]["id"]: r for r in data["results"]}

        assert by_id["a.com"]["account"]["type"] == "maildomain"
        assert by_id["a.com"]["tenant"] == "t1"
        assert by_id["a.com"]["metrics"]["storage_used"] == 2 * overhead

        assert by_id["b.com"]["account"]["type"] == "maildomain"
        assert by_id["b.com"]["tenant"] == "t1"
        assert by_id["b.com"]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_maildomain_excludes_other_custom_attribute(
        self, api_client, url, correctly_configured_header, settings
    ):
        """account_type=maildomain with account_id_key
        only returns domains matching the given attribute value."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"region": "west"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"region": "east"})

        for dom in [domain_a, domain_b]:
            mb = MailboxFactory(domain=dom)
            thread = ThreadFactory()
            ThreadAccessFactory(mailbox=mb, thread=thread)
            contact = ContactFactory(mailbox=mb)
            MessageFactory(thread=thread, sender=contact)

        response = api_client.get(
            url,
            {
                "account_type": "maildomain",
                "account_id_value": "west",
                "account_id_key": "region",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["account"]["id"] == "a.com"
        assert data["results"][0]["region"] == "west"
        assert data["results"][0]["metrics"]["storage_used"] == 1 * overhead

    @pytest.mark.django_db
    def test_organization_full_formula(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Organization aggregation includes overhead, MIME blobs, draft blobs,
        attachments, and templates across multiple mailboxes and domains."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"code": "X1"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"code": "X1"})

        mb_a = MailboxFactory(local_part="alice", domain=domain_a)
        mb_b = MailboxFactory(local_part="bob", domain=domain_b)

        # alice: 2 messages with MIME blobs + 1 attachment
        thread_a = ThreadFactory()
        ThreadAccessFactory(mailbox=mb_a, thread=thread_a)
        contact_a = ContactFactory(mailbox=mb_a)
        msg_a1 = MessageFactory(thread=thread_a, sender=contact_a, raw_mime=b"a1" * 200)
        msg_a2 = MessageFactory(thread=thread_a, sender=contact_a, raw_mime=b"a2" * 300)
        att_a = AttachmentFactory(mailbox=mb_a, blob_size=700)
        att_a.messages.add(msg_a1)

        # alice: 1 signature template
        sig_a = MessageTemplateFactory(
            mailbox=mb_a,
            maildomain=None,
            type=MessageTemplateTypeChoices.SIGNATURE,
        )

        # bob: 1 draft with draft_blob + 1 attachment
        thread_b = ThreadFactory()
        ThreadAccessFactory(mailbox=mb_b, thread=thread_b)
        contact_b = ContactFactory(mailbox=mb_b)
        draft_blob = BlobFactory(mailbox=mb_b, content=b"draft" * 150)
        msg_b = MessageFactory(
            thread=thread_b,
            sender=contact_b,
            is_draft=True,
            draft_blob=draft_blob,
        )
        att_b = AttachmentFactory(mailbox=mb_b, blob_size=900)
        att_b.messages.add(msg_b)

        expected_a = (
            2 * overhead
            + msg_a1.blob.size_compressed
            + msg_a2.blob.size_compressed
            + att_a.blob.size_compressed
            + sig_a.blob.size_compressed
        )
        expected_b = (
            1 * overhead + draft_blob.size_compressed + att_b.blob.size_compressed
        )

        response = api_client.get(
            url,
            {
                "account_type": "organization",
                "account_id_key": "code",
                "account_id_value": "X1",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        assert data["results"][0]["metrics"]["storage_used"] == expected_a + expected_b

    @pytest.mark.django_db
    def test_maildomain_full_formula(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Maildomain aggregation sums the full formula per domain across
        multiple mailboxes with MIME blobs, attachments, and templates."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain = MailDomainFactory(name="a.com", custom_attributes={"code": "X1"})

        mb1 = MailboxFactory(local_part="alice", domain=domain)
        mb2 = MailboxFactory(local_part="bob", domain=domain)

        # alice: 1 message with MIME + 1 attachment
        thread1 = ThreadFactory()
        ThreadAccessFactory(mailbox=mb1, thread=thread1)
        contact1 = ContactFactory(mailbox=mb1)
        msg1 = MessageFactory(thread=thread1, sender=contact1, raw_mime=b"m1" * 200)
        att1 = AttachmentFactory(mailbox=mb1, blob_size=500)
        att1.messages.add(msg1)

        # bob: 2 messages with MIME
        thread2 = ThreadFactory()
        ThreadAccessFactory(mailbox=mb2, thread=thread2)
        contact2 = ContactFactory(mailbox=mb2)
        msg2 = MessageFactory(thread=thread2, sender=contact2, raw_mime=b"m2" * 100)
        msg3 = MessageFactory(thread=thread2, sender=contact2, raw_mime=b"m3" * 150)

        expected_alice = (
            1 * overhead + msg1.blob.size_compressed + att1.blob.size_compressed
        )
        expected_bob = (
            2 * overhead + msg2.blob.size_compressed + msg3.blob.size_compressed
        )

        response = api_client.get(
            url,
            {
                "account_type": "maildomain",
                "account_id_key": "code",
                "account_id_value": "X1",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        assert (
            data["results"][0]["metrics"]["storage_used"]
            == expected_alice + expected_bob
        )

    @pytest.mark.django_db
    def test_organization_shared_thread_counts_per_mailbox(
        self, api_client, url, correctly_configured_header, settings
    ):
        """When two mailboxes in the same org share a thread, the shared
        messages count toward BOTH mailboxes in the org total."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain = MailDomainFactory(name="a.com", custom_attributes={"code": "X1"})
        mb_a = MailboxFactory(local_part="alice", domain=domain)
        mb_b = MailboxFactory(local_part="bob", domain=domain)

        # Shared thread
        shared = ThreadFactory()
        ThreadAccessFactory(mailbox=mb_a, thread=shared)
        ThreadAccessFactory(mailbox=mb_b, thread=shared)
        contact = ContactFactory(mailbox=mb_a)
        msg = MessageFactory(thread=shared, sender=contact, raw_mime=b"shared" * 100)

        # Private thread for alice only
        priv = ThreadFactory()
        ThreadAccessFactory(mailbox=mb_a, thread=priv)
        msg_priv = MessageFactory(thread=priv, sender=contact, raw_mime=b"private" * 50)

        # alice sees 2 messages (shared + private), bob sees 1 (shared)
        expected_alice = (
            2 * overhead + msg.blob.size_compressed + msg_priv.blob.size_compressed
        )
        expected_bob = 1 * overhead + msg.blob.size_compressed

        response = api_client.get(
            url,
            {
                "account_type": "organization",
                "account_id_key": "code",
                "account_id_value": "X1",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 1
        assert (
            data["results"][0]["metrics"]["storage_used"]
            == expected_alice + expected_bob
        )

    @pytest.mark.django_db
    def test_maildomain_shared_thread_across_domains(
        self, api_client, url, correctly_configured_header, settings
    ):
        """A shared thread between mailboxes on different domains counts
        toward each domain independently."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com", custom_attributes={"code": "X1"})
        domain_b = MailDomainFactory(name="b.com", custom_attributes={"code": "X1"})

        mb_a = MailboxFactory(local_part="alice", domain=domain_a)
        mb_b = MailboxFactory(local_part="bob", domain=domain_b)

        # Shared thread with 2 messages
        shared = ThreadFactory()
        ThreadAccessFactory(mailbox=mb_a, thread=shared)
        ThreadAccessFactory(mailbox=mb_b, thread=shared)
        contact = ContactFactory(mailbox=mb_a)
        msg1 = MessageFactory(thread=shared, sender=contact, raw_mime=b"s1" * 100)
        msg2 = MessageFactory(thread=shared, sender=contact, raw_mime=b"s2" * 200)

        shared_blob_size = msg1.blob.size_compressed + msg2.blob.size_compressed

        response = api_client.get(
            url,
            {
                "account_type": "maildomain",
                "account_id_key": "code",
                "account_id_value": "X1",
            },
            **correctly_configured_header,
        )
        data = response.json()

        assert data["count"] == 2
        by_id = {r["account"]["id"]: r for r in data["results"]}
        # Both domains see the full shared thread
        assert by_id["a.com"]["metrics"]["storage_used"] == (
            2 * overhead + shared_blob_size
        )
        assert by_id["b.com"]["metrics"]["storage_used"] == (
            2 * overhead + shared_blob_size
        )

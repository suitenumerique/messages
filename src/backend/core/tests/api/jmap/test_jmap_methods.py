"""Tests for JMAP methods using jmapc library."""

from datetime import timedelta

from django.utils import timezone

import pytest
from jmapc import Ref
from jmapc.methods import (
    EmailGet,
    EmailQuery,
    MailboxGet,
    MailboxQuery,
    ThreadGet,
)

from core import enums, factories

pytestmark = pytest.mark.django_db


class TestMailboxQuery:
    """Tests for Mailbox/query method."""

    def test_mailbox_query_returns_accessible_mailboxes(self, jmap_client, mailbox):
        """Test that Mailbox/query returns mailboxes the user has access to."""
        result = jmap_client.request(MailboxQuery())

        assert str(mailbox.id) in result.ids

    def test_mailbox_query_filters_by_name(self, jmap_client, user):
        """Test that Mailbox/query can filter by name."""
        import uuid

        # Create mailboxes with specific names using unique domain
        domain_name = f"filter-{uuid.uuid4().hex[:8]}.com"
        domain = factories.MailDomainFactory(name=domain_name)
        mailbox1 = factories.MailboxFactory(
            local_part="inbox",
            domain=domain,
            users_read=[user],
        )
        factories.MailboxFactory(
            local_part="other",
            domain=domain,
            users_read=[user],
        )

        result = jmap_client.request(
            MailboxQuery(filter={"name": f"inbox@{domain_name}"})
        )

        assert str(mailbox1.id) in result.ids
        assert len(result.ids) == 1

    def test_mailbox_query_excludes_inaccessible_mailboxes(self, jmap_client, user):
        """Test that Mailbox/query excludes mailboxes without access."""
        # Create a mailbox the user can access
        accessible = factories.MailboxFactory(users_read=[user])
        # Create a mailbox the user cannot access
        inaccessible = factories.MailboxFactory()

        result = jmap_client.request(MailboxQuery())

        assert str(accessible.id) in result.ids
        assert str(inaccessible.id) not in result.ids


class TestMailboxGet:
    """Tests for Mailbox/get method."""

    def test_mailbox_get_returns_mailbox_details(self, jmap_client, mailbox):
        """Test that Mailbox/get returns mailbox details."""
        result = jmap_client.request(MailboxGet(ids=[str(mailbox.id)]))

        assert len(result.list) == 1
        mailbox_data = result.list[0]
        assert mailbox_data["id"] == str(mailbox.id)
        assert "name" in mailbox_data
        assert "totalEmails" in mailbox_data
        assert "unreadEmails" in mailbox_data

    def test_mailbox_get_with_back_reference(self, jmap_client, user):
        """Test that Mailbox/get works with back-references from Mailbox/query."""
        mailbox = factories.MailboxFactory(
            local_part="test", domain__name="backref-example.com", users_read=[user]
        )

        results = jmap_client.request(
            [
                MailboxQuery(filter={"name": "test@backref-example.com"}),
                MailboxGet(ids=Ref("/ids")),  # References ids from previous method
            ]
        )

        assert str(mailbox.id) in results[0].ids
        assert len(results[1].list) == 1
        assert results[1].list[0]["id"] == str(mailbox.id)

    def test_mailbox_get_returns_not_found(self, jmap_client):
        """Test that Mailbox/get returns notFound for non-existent IDs."""
        fake_id = "00000000-0000-0000-0000-000000000000"

        result = jmap_client.request(MailboxGet(ids=[fake_id]))

        assert len(result.list) == 0
        assert fake_id in result.not_found


class TestEmailQuery:
    """Tests for Email/query method."""

    def test_email_query_returns_emails(self, jmap_client, mailbox_with_threads):
        """Test that Email/query returns emails the user has access to."""
        result = jmap_client.request(EmailQuery())

        assert len(result.ids) > 0

    def test_email_query_filters_by_mailbox(self, jmap_client, user):
        """Test that Email/query filters by inMailbox."""
        # Create two mailboxes with messages
        mailbox1 = factories.MailboxFactory(users_read=[user])
        mailbox2 = factories.MailboxFactory(users_read=[user])

        thread1 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox1, thread=thread1, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender1 = factories.ContactFactory(mailbox=mailbox1)
        msg1 = factories.MessageFactory(thread=thread1, sender=sender1)

        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox2, thread=thread2, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender2 = factories.ContactFactory(mailbox=mailbox2)
        factories.MessageFactory(thread=thread2, sender=sender2)

        result = jmap_client.request(EmailQuery(filter={"inMailbox": str(mailbox1.id)}))

        assert str(msg1.id) in result.ids

    def test_email_query_with_collapse_threads(self, jmap_client, user):
        """Test that Email/query with collapseThreads returns one email per thread."""
        mailbox = factories.MailboxFactory(users_read=[user])
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(mailbox=mailbox)

        # Create 3 messages in the same thread
        for i in range(3):
            factories.MessageFactory(
                thread=thread,
                sender=sender,
                created_at=timezone.now() - timedelta(hours=i),
            )

        result = jmap_client.request(EmailQuery(collapse_threads=True))

        # Should only return 1 email (the latest) since all are in the same thread
        thread_ids_seen = set()
        for email_id in result.ids:
            # Get the email to check its threadId
            email_result = jmap_client.request(EmailGet(ids=[email_id]))
            thread_id = email_result.list[0]["threadId"]
            assert thread_id not in thread_ids_seen, "Same thread appeared twice"
            thread_ids_seen.add(thread_id)

    def test_email_query_sorts_by_received_at(self, jmap_client, user):
        """Test that Email/query sorts by receivedAt."""
        mailbox = factories.MailboxFactory(users_read=[user])
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(mailbox=mailbox)

        # Create messages at different times
        msg_old = factories.MessageFactory(
            thread=thread, sender=sender, created_at=timezone.now() - timedelta(days=2)
        )
        msg_new = factories.MessageFactory(
            thread=thread, sender=sender, created_at=timezone.now()
        )

        result = jmap_client.request(
            EmailQuery(sort=[{"property": "receivedAt", "isAscending": False}])
        )

        # Newest should be first
        assert result.ids.index(str(msg_new.id)) < result.ids.index(str(msg_old.id))


class TestEmailGet:
    """Tests for Email/get method."""

    def test_email_get_returns_email_details(self, jmap_client, user):
        """Test that Email/get returns email details."""
        mailbox = factories.MailboxFactory(users_read=[user])
        thread = factories.ThreadFactory(subject="Test Subject")
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(
            name="Sender", email="sender@test.com", mailbox=mailbox
        )
        message = factories.MessageFactory(
            thread=thread,
            subject="Test Email",
            sender=sender,
        )

        result = jmap_client.request(EmailGet(ids=[str(message.id)]))

        assert len(result.list) == 1
        email = result.list[0]
        assert email["id"] == str(message.id)
        assert email["threadId"] == str(thread.id)
        assert email["subject"] == "Test Email"
        assert email["from"][0]["email"] == "sender@test.com"

    def test_email_get_with_properties(self, jmap_client, user):
        """Test that Email/get respects the properties parameter."""
        mailbox = factories.MailboxFactory(users_read=[user])
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(mailbox=mailbox)
        message = factories.MessageFactory(thread=thread, sender=sender)

        result = jmap_client.request(
            EmailGet(ids=[str(message.id)], properties=["threadId", "subject"])
        )

        email = result.list[0]
        assert "id" in email  # id is always included
        assert "threadId" in email
        assert "subject" in email
        assert "from" not in email  # not requested

    def test_email_get_includes_keywords(self, jmap_client, user):
        """Test that Email/get includes keywords (flags)."""
        mailbox = factories.MailboxFactory(users_read=[user])
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(mailbox=mailbox)
        message = factories.MessageFactory(
            thread=thread,
            sender=sender,
            is_unread=False,  # $seen
            is_starred=True,  # $flagged
            is_draft=True,  # $draft
        )

        result = jmap_client.request(EmailGet(ids=[str(message.id)]))

        keywords = result.list[0]["keywords"]
        assert keywords.get("$seen") is True
        assert keywords.get("$flagged") is True
        assert keywords.get("$draft") is True


class TestThreadGet:
    """Tests for Thread/get method."""

    def test_thread_get_returns_thread_with_email_ids(self, jmap_client, user):
        """Test that Thread/get returns thread with emailIds."""
        mailbox = factories.MailboxFactory(users_read=[user])
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        sender = factories.ContactFactory(mailbox=mailbox)

        # Create multiple messages in thread
        msg1 = factories.MessageFactory(
            thread=thread, sender=sender, created_at=timezone.now() - timedelta(hours=2)
        )
        msg2 = factories.MessageFactory(
            thread=thread, sender=sender, created_at=timezone.now() - timedelta(hours=1)
        )
        msg3 = factories.MessageFactory(
            thread=thread, sender=sender, created_at=timezone.now()
        )

        result = jmap_client.request(ThreadGet(ids=[str(thread.id)]))

        assert len(result.list) == 1
        thread_data = result.list[0]
        assert thread_data["id"] == str(thread.id)
        assert len(thread_data["emailIds"]) == 3

        # Check order (oldest to newest)
        assert thread_data["emailIds"] == [str(msg1.id), str(msg2.id), str(msg3.id)]

    def test_thread_get_returns_not_found(self, jmap_client):
        """Test that Thread/get returns notFound for non-existent IDs."""
        fake_id = "00000000-0000-0000-0000-000000000000"

        result = jmap_client.request(ThreadGet(ids=[fake_id]))

        assert len(result.list) == 0
        assert fake_id in result.not_found

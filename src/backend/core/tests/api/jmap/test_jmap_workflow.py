"""End-to-end workflow tests for JMAP API using jmapc library.

These tests replicate the workflow from jmapc's recent_threads.py example.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

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

from core import enums, factories, models
from core.tests.api.jmap.test_jmap_methods import (
    EmailSet,
    EmailSubmissionSet,
    IdentityGet,
)

pytestmark = pytest.mark.django_db


class TestRecentThreadsWorkflow:
    """Tests for the 'read recent threads' workflow from jmapc example."""

    def test_recent_threads_full_workflow(self, jmap_client, user):
        """
        Test the complete 'recent threads' workflow:
        1. Find the inbox mailbox
        2. Query recent emails with collapseThreads
        3. Get thread details for each email
        """
        # Setup: Create a mailbox with threads and messages
        # Use unique domain name to avoid conflicts
        domain_name = f"workflow-{uuid.uuid4().hex[:8]}.com"
        mailbox = factories.MailboxFactory(
            local_part="inbox",
            domain__name=domain_name,
            users_read=[user],
        )

        # Create 3 threads with multiple messages
        threads_data = []
        for i in range(3):
            thread = factories.ThreadFactory(subject=f"Thread {i}")
            factories.ThreadAccessFactory(
                mailbox=mailbox,
                thread=thread,
                role=enums.ThreadAccessRoleChoices.EDITOR,
            )
            sender = factories.ContactFactory(mailbox=mailbox)

            messages = []
            for j in range(2):  # 2 messages per thread
                msg = factories.MessageFactory(
                    thread=thread,
                    subject=f"Message {j} in Thread {i}",
                    sender=sender,
                    created_at=timezone.now() - timedelta(days=i, hours=j),
                )
                messages.append(msg)

            thread.update_stats()
            threads_data.append({"thread": thread, "messages": messages})

        # Step 1: Find the inbox mailbox
        results = jmap_client.request(
            [
                MailboxQuery(filter={"name": "inbox"}),
                MailboxGet(ids=Ref("/ids")),
            ]
        )

        assert len(results[0].ids) == 1
        assert str(mailbox.id) in results[0].ids
        inbox = results[1].list[0]
        assert inbox["name"] == f"inbox@{domain_name}"

        # Step 2: Query recent emails with collapseThreads
        results = jmap_client.request(
            [
                EmailQuery(
                    filter={"inMailbox": inbox["id"]},
                    sort=[{"property": "receivedAt", "isAscending": False}],
                    collapse_threads=True,
                    limit=5,
                ),
                EmailGet(
                    ids=Ref("/ids"),
                    properties=["threadId", "subject", "from", "receivedAt"],
                ),
            ]
        )

        # Should get one email per thread (3 threads, but collapseThreads)
        query_result = results[0]
        assert len(query_result.ids) == 3

        emails = results[1].list
        assert len(emails) == 3

        # Each email should have the required properties
        for email in emails:
            assert "threadId" in email
            assert "subject" in email
            assert "from" in email
            assert "receivedAt" in email

        # Step 3: Get thread details
        thread_ids = [email["threadId"] for email in emails]
        results = jmap_client.request(ThreadGet(ids=thread_ids))

        assert len(results.list) == 3
        for thread in results.list:
            assert "id" in thread
            assert "emailIds" in thread
            assert len(thread["emailIds"]) == 2  # Each thread has 2 messages

    def test_workflow_with_date_filter(self, jmap_client, user):
        """Test the workflow with a date filter (like 7 days in jmapc example)."""
        domain_name = f"datefilter-{uuid.uuid4().hex[:8]}.com"
        mailbox = factories.MailboxFactory(
            local_part="inbox",
            domain__name=domain_name,
            users_read=[user],
        )

        sender = factories.ContactFactory(mailbox=mailbox)

        # Create recent thread (within 7 days)
        recent_thread = factories.ThreadFactory(subject="Recent")
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=recent_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        recent_msg = factories.MessageFactory(
            thread=recent_thread,
            sender=sender,
        )
        # Update created_at directly in DB (auto_now_add prevents setting it on create)
        from core.models import Message

        Message.objects.filter(id=recent_msg.id).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        recent_msg.refresh_from_db()

        # Create old thread (older than 7 days)
        old_thread = factories.ThreadFactory(subject="Old")
        factories.ThreadAccessFactory(
            mailbox=mailbox,
            thread=old_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        old_msg = factories.MessageFactory(
            thread=old_thread,
            sender=sender,
        )
        Message.objects.filter(id=old_msg.id).update(
            created_at=timezone.now() - timedelta(days=14)
        )

        # Query emails from last 7 days
        seven_days_ago = (timezone.now() - timedelta(days=7)).isoformat()

        result = jmap_client.request(
            EmailQuery(
                filter={
                    "inMailbox": str(mailbox.id),
                    "after": seven_days_ago,
                },
                collapse_threads=True,
            )
        )

        # Should only get the recent email
        assert len(result.ids) == 1, (
            f"Expected 1 message, got {len(result.ids)}: {result.ids}"
        )
        assert str(recent_msg.id) in result.ids

    def test_workflow_empty_mailbox(self, jmap_client, user):
        """Test the workflow with an empty mailbox."""
        domain_name = f"empty-{uuid.uuid4().hex[:8]}.com"
        mailbox = factories.MailboxFactory(
            local_part="empty",
            domain__name=domain_name,
            users_read=[user],
        )

        # Query emails in empty mailbox
        result = jmap_client.request(
            EmailQuery(
                filter={"inMailbox": str(mailbox.id)},
                collapse_threads=True,
            )
        )

        assert len(result.ids) == 0

    def test_workflow_multiple_mailboxes(self, jmap_client, user):
        """Test the workflow with multiple mailboxes."""
        # Create two mailboxes on the same domain
        domain_name = f"multi-{uuid.uuid4().hex[:8]}.com"
        domain = factories.MailDomainFactory(name=domain_name)
        inbox = factories.MailboxFactory(
            local_part="inbox",
            domain=domain,
            users_read=[user],
        )
        sent = factories.MailboxFactory(
            local_part="sent",
            domain=domain,
            users_read=[user],
        )

        # Add messages to each
        inbox_sender = factories.ContactFactory(mailbox=inbox)
        inbox_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=inbox,
            thread=inbox_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=inbox_thread, sender=inbox_sender)

        sent_sender = factories.ContactFactory(mailbox=sent)
        sent_thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            mailbox=sent,
            thread=sent_thread,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=sent_thread, sender=sent_sender)

        # Query all mailboxes
        result = jmap_client.request(MailboxQuery())

        # Should find both mailboxes
        assert len(result.ids) == 2
        assert str(inbox.id) in result.ids
        assert str(sent.id) in result.ids

        # Query emails in inbox only
        inbox_result = jmap_client.request(
            EmailQuery(filter={"inMailbox": str(inbox.id)})
        )

        # Should only get inbox emails
        assert len(inbox_result.ids) == 1


class TestSendWorkflow:
    """Tests for the full message sending workflow."""

    @patch("core.api.jmap.methods.send_message_task")
    def test_full_send_workflow(self, mock_send_task, jmap_client, user):
        """
        Test the complete send workflow:
        1. Identity/get - discover sending identities
        2. Email/set create - create a draft
        3. EmailSubmission/set create - submit for delivery
        4. Verify message is no longer a draft
        """
        mailbox = factories.MailboxFactory(users_read=[user])

        # Step 1: Get identities
        identity_result = jmap_client.request(IdentityGet())

        assert len(identity_result.list) >= 1
        identity = next(
            i for i in identity_result.list if i["id"] == str(mailbox.id)
        )
        identity_id = identity["id"]
        sender_email = identity["email"]

        # Step 2: Create a draft
        create_result = jmap_client.request(
            EmailSet(
                create={
                    "draft1": {
                        "mailboxIds": {identity_id: True},
                        "subject": "Workflow Test Email",
                        "from": [{"name": "Me", "email": sender_email}],
                        "to": [
                            {"name": "Recipient", "email": "recipient@example.com"}
                        ],
                        "bodyValues": {
                            "text": {"value": "Hello from the workflow test!"},
                            "html": {
                                "value": "<p>Hello from the workflow test!</p>"
                            },
                        },
                        "textBody": [{"partId": "text", "type": "text/plain"}],
                        "htmlBody": [{"partId": "html", "type": "text/html"}],
                        "keywords": {"$draft": True},
                    }
                }
            )
        )

        assert create_result.data["created"] is not None
        draft = create_result.data["created"]["draft1"]
        email_id = draft["id"]
        thread_id = draft["threadId"]

        # Verify draft was created correctly
        message = models.Message.objects.get(id=email_id)
        assert message.is_draft is True
        assert message.subject == "Workflow Test Email"

        # Step 3: Submit for delivery
        submit_result = jmap_client.request(
            EmailSubmissionSet(
                create={
                    "sub1": {
                        "emailId": email_id,
                        "identityId": identity_id,
                    }
                }
            )
        )

        assert submit_result.data["created"] is not None
        submission = submit_result.data["created"]["sub1"]
        assert submission["emailId"] == email_id
        assert submission["threadId"] == thread_id
        assert submission["undoStatus"] == "final"

        # Step 4: Verify message is no longer a draft
        message.refresh_from_db()
        assert message.is_draft is False

        # Verify send task was queued
        mock_send_task.delay.assert_called_once_with(email_id)

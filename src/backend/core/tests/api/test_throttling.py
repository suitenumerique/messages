"""Tests for API throttling."""
# pylint: disable=redefined-outer-name

from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories


@pytest.fixture
def mailbox_with_contact():
    """Create a mailbox with a contact."""
    mailbox = factories.MailboxFactory()
    contact = factories.ContactFactory(
        email=mailbox.local_part + "@" + mailbox.domain.name
    )
    mailbox.contact = contact
    mailbox.save()
    return mailbox


@pytest.fixture
def mailbox_with_contact_and_user(mailbox_with_contact):
    """Create a mailbox with a contact and a user."""
    user = factories.UserFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox_with_contact, user=user, role=enums.MailboxRoleChoices.SENDER
    )
    return mailbox_with_contact, user


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestOutboundThrottleBurst:
    """Test burst throttling on the send message endpoint per mailbox."""

    def test_send_message_burst_throttle_limits_requests(
        self, mailbox_with_contact_and_user
    ):
        """Test that send message endpoint is throttled after burst limit."""
        mailbox, user = mailbox_with_contact_and_user

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("send-message")

        # Create draft messages to send
        drafts = []
        for _ in range(4):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
            message = factories.MessageFactory(
                thread=thread,
                sender=mailbox.contact,
                is_draft=True,
                is_sender=True,
            )
            factories.MessageRecipientFactory(message=message)
            drafts.append(message)

        # Mock burst rate to 2/minute for testing
        with patch(
            "core.api.throttling.OutboundThrottleBurst.get_rate",
            return_value="2/minute",
        ):
            # First 2 requests should not be throttled
            for i in range(2):
                response = client.post(
                    url,
                    {
                        "messageId": str(drafts[i].id),
                        "senderId": str(mailbox.id),
                        "textBody": f"Test message {i}",
                    },
                    format="json",
                )
                assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                    f"Request {i + 1} was unexpectedly throttled"
                )

            # 3rd request should be throttled
            response = client.post(
                url,
                {
                    "messageId": str(drafts[2].id),
                    "senderId": str(mailbox.id),
                    "textBody": "Test message 3",
                },
                format="json",
            )
            assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestOutboundThrottleSustained:
    """Test sustained throttling on the send message endpoint per mailbox."""

    def test_send_message_sustained_throttle_limits_requests(
        self, mailbox_with_contact_and_user
    ):
        """Test that send message endpoint is throttled after sustained limit."""
        mailbox, user = mailbox_with_contact_and_user

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("send-message")

        # Create draft messages to send
        drafts = []
        for _ in range(4):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
            message = factories.MessageFactory(
                thread=thread,
                sender=mailbox.contact,
                is_draft=True,
                is_sender=True,
            )
            factories.MessageRecipientFactory(message=message)
            drafts.append(message)

        # Mock sustained rate to 2/hour for testing
        with patch(
            "core.api.throttling.OutboundThrottleSustained.get_rate",
            return_value="2/hour",
        ):
            # First 2 requests should not be throttled
            for i in range(2):
                response = client.post(
                    url,
                    {
                        "messageId": str(drafts[i].id),
                        "senderId": str(mailbox.id),
                        "textBody": f"Test message {i}",
                    },
                    format="json",
                )
                assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                    f"Request {i + 1} was unexpectedly throttled"
                )

            # 3rd request should be throttled
            response = client.post(
                url,
                {
                    "messageId": str(drafts[2].id),
                    "senderId": str(mailbox.id),
                    "textBody": "Test message 3",
                },
                format="json",
            )
            assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestOutboundThrottlePerMailbox:
    """Test that throttle is per mailbox, not per user."""

    def test_throttle_is_per_mailbox_not_user(self, mailbox_with_contact_and_user):
        """Test that different mailboxes have separate throttle counters."""
        mailbox1, user = mailbox_with_contact_and_user

        # Create a second mailbox for the same user
        mailbox2 = factories.MailboxFactory()
        contact2 = factories.ContactFactory(
            email=mailbox2.local_part + "@" + mailbox2.domain.name
        )
        mailbox2.contact = contact2
        mailbox2.save()
        factories.MailboxAccessFactory(
            mailbox=mailbox2, user=user, role=enums.MailboxRoleChoices.SENDER
        )

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("send-message")

        # Create drafts for mailbox1 (2 drafts)
        drafts1 = []
        for _ in range(2):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(thread=thread, mailbox=mailbox1)
            draft = factories.MessageFactory(
                thread=thread,
                sender=mailbox1.contact,
                is_draft=True,
                is_sender=True,
            )
            factories.MessageRecipientFactory(message=draft)
            drafts1.append(draft)

        # Create draft for mailbox2
        thread2 = factories.ThreadFactory()
        factories.ThreadAccessFactory(thread=thread2, mailbox=mailbox2)
        draft2 = factories.MessageFactory(
            thread=thread2,
            sender=mailbox2.contact,
            is_draft=True,
            is_sender=True,
        )
        factories.MessageRecipientFactory(message=draft2)

        with patch(
            "core.api.throttling.OutboundThrottleBurst.get_rate",
            return_value="1/minute",
        ):
            # Mailbox 1 - first message → OK
            response1 = client.post(
                url,
                {
                    "messageId": str(drafts1[0].id),
                    "senderId": str(mailbox1.id),
                    "textBody": "Test from mailbox 1",
                },
                format="json",
            )
            assert response1.status_code != status.HTTP_429_TOO_MANY_REQUESTS

            # Mailbox 1 - second message → Throttled (1/minute limit)
            response1_throttled = client.post(
                url,
                {
                    "messageId": str(drafts1[1].id),
                    "senderId": str(mailbox1.id),
                    "textBody": "Test from mailbox 1 - second",
                },
                format="json",
            )
            assert response1_throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS

            # Mailbox 2 - first message → OK (different mailbox, not affected)
            response2 = client.post(
                url,
                {
                    "messageId": str(draft2.id),
                    "senderId": str(mailbox2.id),
                    "textBody": "Test from mailbox 2",
                },
                format="json",
            )
            assert response2.status_code != status.HTTP_429_TOO_MANY_REQUESTS

    def test_throttle_shared_between_users_same_mailbox(
        self, mailbox_with_contact_and_user
    ):
        """Test that throttle is shared between users for the same mailbox."""
        mailbox, user1 = mailbox_with_contact_and_user
        # Add second user with sender permissions to the same mailbox
        user2 = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user2, role=enums.MailboxRoleChoices.SENDER
        )

        client1 = APIClient()
        client1.force_authenticate(user=user1)
        client2 = APIClient()
        client2.force_authenticate(user=user2)

        url = reverse("send-message")

        # Create drafts - both users have access via the shared mailbox
        drafts = []
        for _ in range(2):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
            draft = factories.MessageFactory(
                thread=thread,
                sender=mailbox.contact,
                is_draft=True,
                is_sender=True,
            )
            factories.MessageRecipientFactory(message=draft)
            drafts.append(draft)

        with patch(
            "core.api.throttling.OutboundThrottleBurst.get_rate",
            return_value="1/minute",
        ):
            # User 1 sends from shared mailbox → OK
            response1 = client1.post(
                url,
                {
                    "messageId": str(drafts[0].id),
                    "senderId": str(mailbox.id),
                    "textBody": "Test from user 1",
                },
                format="json",
            )
            assert response1.status_code != status.HTTP_429_TOO_MANY_REQUESTS

            # User 2 sends from SAME mailbox → Throttled (same mailbox quota)
            response2 = client2.post(
                url,
                {
                    "messageId": str(drafts[1].id),
                    "senderId": str(mailbox.id),
                    "textBody": "Test from user 2",
                },
                format="json",
            )
            assert response2.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestOutboundThrottleDomainOverride:
    """Test that maildomain can override throttle rates."""

    def test_maildomain_can_override_burst_rate(self, mailbox_with_contact_and_user):
        """Test that maildomain can set a custom burst throttle rate."""
        mailbox, user = mailbox_with_contact_and_user
        # Set custom burst throttle rate on the domain (high limit)
        mailbox.domain.custom_settings = {
            "outbound_message_throttle_rate_burst": "100/minute"
        }
        mailbox.domain.save()

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("send-message")

        # Create 5 drafts
        drafts = []
        for _ in range(5):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
            draft = factories.MessageFactory(
                thread=thread,
                sender=mailbox.contact,
                is_draft=True,
                is_sender=True,
            )
            factories.MessageRecipientFactory(message=draft)
            drafts.append(draft)

        # All 5 should succeed (domain allows 100/minute)
        for i, draft in enumerate(drafts):
            response = client.post(
                url,
                {
                    "messageId": str(draft.id),
                    "senderId": str(mailbox.id),
                    "textBody": f"Test message {i}",
                },
                format="json",
            )
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                f"Request {i + 1} was unexpectedly throttled despite domain override"
            )

    def test_maildomain_can_override_sustained_rate(
        self, mailbox_with_contact_and_user
    ):
        """Test that maildomain can set a custom sustained throttle rate."""
        mailbox, user = mailbox_with_contact_and_user
        # Set custom sustained throttle rate on the domain (high limit)
        mailbox.domain.custom_settings = {
            "outbound_message_throttle_rate_sustained": "500/hour"
        }
        mailbox.domain.save()

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("send-message")

        # Create 5 drafts
        drafts = []
        for _ in range(5):
            thread = factories.ThreadFactory()
            factories.ThreadAccessFactory(thread=thread, mailbox=mailbox)
            draft = factories.MessageFactory(
                thread=thread,
                sender=mailbox.contact,
                is_draft=True,
                is_sender=True,
            )
            factories.MessageRecipientFactory(message=draft)
            drafts.append(draft)

        # All 5 should succeed (domain allows 500/hour)
        for i, draft in enumerate(drafts):
            response = client.post(
                url,
                {
                    "messageId": str(draft.id),
                    "senderId": str(mailbox.id),
                    "textBody": f"Test message {i}",
                },
                format="json",
            )
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                f"Request {i + 1} was unexpectedly throttled despite domain override"
            )


# =============================================================================
# Inbound Throttling Tests
# =============================================================================


@pytest.fixture
def channel_with_mailbox():
    """Create a channel with a mailbox for widget testing."""
    mailbox = factories.MailboxFactory()
    contact = factories.ContactFactory(
        email=mailbox.local_part + "@" + mailbox.domain.name
    )
    mailbox.contact = contact
    mailbox.save()
    channel = factories.ChannelFactory(mailbox=mailbox, type="widget")
    return channel, mailbox


@pytest.mark.django_db
class TestInboundThrottleBurst:
    """Test burst throttling on inbound message endpoints."""

    def test_inbound_widget_burst_throttle_limits_requests(self, channel_with_mailbox):
        """Test that inbound widget endpoint is throttled after burst limit."""
        channel, _ = channel_with_mailbox

        client = APIClient()
        url = reverse("inbound-widget-deliver")

        with patch(
            "core.api.throttling.InboundThrottleBurst.get_rate", return_value="2/minute"
        ):
            # First 2 requests should not be throttled
            for i in range(2):
                response = client.post(
                    url,
                    {
                        "email": f"sender{i}@example.com",
                        "textBody": f"Test message {i}",
                    },
                    format="json",
                    HTTP_X_CHANNEL_ID=str(channel.id),
                )
                assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                    f"Request {i + 1} was unexpectedly throttled"
                )

            # 3rd request should be throttled
            response = client.post(
                url,
                {
                    "email": "sender3@example.com",
                    "textBody": "Test message 3",
                },
                format="json",
                HTTP_X_CHANNEL_ID=str(channel.id),
            )
            assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestInboundThrottleSustained:
    """Test sustained throttling on inbound message endpoints."""

    def test_inbound_widget_sustained_throttle_limits_requests(
        self, channel_with_mailbox
    ):
        """Test that inbound widget endpoint is throttled after sustained limit."""
        channel, _ = channel_with_mailbox

        client = APIClient()
        url = reverse("inbound-widget-deliver")

        with patch(
            "core.api.throttling.InboundThrottleSustained.get_rate",
            return_value="2/hour",
        ):
            # First 2 requests should not be throttled
            for i in range(2):
                response = client.post(
                    url,
                    {
                        "email": f"sender{i}@example.com",
                        "textBody": f"Test message {i}",
                    },
                    format="json",
                    HTTP_X_CHANNEL_ID=str(channel.id),
                )
                assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS, (
                    f"Request {i + 1} was unexpectedly throttled"
                )

            # 3rd request should be throttled
            response = client.post(
                url,
                {
                    "email": "sender3@example.com",
                    "textBody": "Test message 3",
                },
                format="json",
                HTTP_X_CHANNEL_ID=str(channel.id),
            )
            assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestInboundThrottlePerIP:
    """Test that inbound throttle is per IP address."""

    def test_inbound_throttle_is_per_ip(self, channel_with_mailbox):
        """Test that different IPs have separate throttle counters."""
        channel, _ = channel_with_mailbox

        client = APIClient()
        url = reverse("inbound-widget-deliver")

        with patch(
            "core.api.throttling.InboundThrottleBurst.get_rate", return_value="1/minute"
        ):
            # First request from IP 1 → OK
            response1 = client.post(
                url,
                {
                    "email": "sender1@example.com",
                    "textBody": "Test from IP 1",
                },
                format="json",
                HTTP_X_CHANNEL_ID=str(channel.id),
                REMOTE_ADDR="192.168.1.1",
            )
            assert response1.status_code != status.HTTP_429_TOO_MANY_REQUESTS

            # Second request from IP 1 → Throttled
            response1_throttled = client.post(
                url,
                {
                    "email": "sender2@example.com",
                    "textBody": "Test from IP 1 again",
                },
                format="json",
                HTTP_X_CHANNEL_ID=str(channel.id),
                REMOTE_ADDR="192.168.1.1",
            )
            assert response1_throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS

            # Request from IP 2 → OK (different IP, not affected)
            response2 = client.post(
                url,
                {
                    "email": "sender3@example.com",
                    "textBody": "Test from IP 2",
                },
                format="json",
                HTTP_X_CHANNEL_ID=str(channel.id),
                REMOTE_ADDR="192.168.1.2",
            )
            assert response2.status_code != status.HTTP_429_TOO_MANY_REQUESTS

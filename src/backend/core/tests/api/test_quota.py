"""Tests for recipient quota endpoints and utilities."""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories
from core.enums import MailboxRoleChoices, MailDomainAccessRoleChoices
from core.services.quota import get_period_display, get_period_start

pytestmark = pytest.mark.django_db


class TestQuotaUtilities:
    """Test quota utility functions."""

    def test_get_period_display_day(self):
        """Test period display for daily quota."""
        assert get_period_display("d") == "day"

    def test_get_period_display_month(self):
        """Test period display for monthly quota."""
        assert get_period_display("m") == "month"

    def test_get_period_display_year(self):
        """Test period display for yearly quota."""
        assert get_period_display("y") == "year"

    def test_get_period_display_unknown(self):
        """Test period display for unknown period returns the period itself."""
        assert get_period_display("x") == "x"

    def test_get_period_start_day(self):
        """Test period start for daily quota."""
        now = datetime(2025, 6, 15, 14, 30, 45, 123456, tzinfo=dt_timezone.utc)
        period_start = get_period_start("d", now)
        assert period_start == datetime(2025, 6, 15, 0, 0, 0, 0, tzinfo=dt_timezone.utc)

    def test_get_period_start_month(self):
        """Test period start for monthly quota."""
        now = datetime(2025, 6, 15, 14, 30, 45, 123456, tzinfo=dt_timezone.utc)
        period_start = get_period_start("m", now)
        assert period_start == datetime(2025, 6, 1, 0, 0, 0, 0, tzinfo=dt_timezone.utc)

    def test_get_period_start_year(self):
        """Test period start for yearly quota."""
        now = datetime(2025, 6, 15, 14, 30, 45, 123456, tzinfo=dt_timezone.utc)
        period_start = get_period_start("y", now)
        assert period_start == datetime(2025, 1, 1, 0, 0, 0, 0, tzinfo=dt_timezone.utc)

    def test_get_period_start_unknown(self):
        """Test period start for unknown period returns now."""
        now = datetime(2025, 6, 15, 14, 30, 45, 123456, tzinfo=dt_timezone.utc)
        period_start = get_period_start("x", now)
        assert period_start == now

    def test_get_period_start_defaults_to_now(self):
        """Test period start uses current time when now is not provided."""
        # Without passing now, function should use current time
        period_start = get_period_start("d")
        # Just verify it returns a datetime at midnight
        assert period_start.hour == 0
        assert period_start.minute == 0
        assert period_start.second == 0
        assert period_start.microsecond == 0


class TestMailboxQuotaEndpoint:
    """Test mailbox quota API endpoint."""

    @override_settings(MAX_RECIPIENTS_FOR_MAILBOX="1000/m")
    def test_mailbox_quota_as_sender(self):
        """Test that a sender can access their mailbox quota."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        # Create access for this user to the mailbox
        factories.MailboxAccessFactory(
            mailbox=mailbox, user=user, role=MailboxRoleChoices.SENDER
        )

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("mailboxes-quota", kwargs={"pk": mailbox.id})
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "period" in data
        assert "period_display" in data
        assert "period_start" in data
        assert "recipient_count" in data
        assert "quota_limit" in data
        assert "remaining" in data
        assert "usage_percentage" in data

    def test_mailbox_quota_no_access(self):
        """Test that users without access get 404."""
        mailbox = factories.MailboxFactory()
        user = factories.UserFactory()
        # No access created for this user

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse("mailboxes-quota", kwargs={"pk": mailbox.id})
        response = client.get(url)

        # Returns 404 because queryset filters by user access
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_mailbox_quota_unauthenticated(self):
        """Test that unauthenticated users cannot access quota."""
        mailbox = factories.MailboxFactory()
        client = APIClient()

        url = reverse("mailboxes-quota", kwargs={"pk": mailbox.id})
        response = client.get(url)

        # Returns 401 for unauthenticated
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


class TestMaildomainQuotaEndpoint:
    """Test maildomain quota API endpoint."""

    @override_settings(MAX_RECIPIENTS_FOR_DOMAIN="5000/m")
    def test_maildomain_quota_as_admin(self):
        """Test that a domain admin can access maildomain quota."""
        user = factories.UserFactory()
        maildomain = factories.MailDomainFactory()
        factories.MailDomainAccessFactory(
            maildomain=maildomain, user=user, role=MailDomainAccessRoleChoices.ADMIN
        )

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse(
            "admin-maildomains-quota", kwargs={"maildomain_pk": maildomain.id}
        )
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["period"] in ["d", "m", "y"]
        assert data["period_display"] in ["day", "month", "year"]
        assert "period_start" in data
        assert data["recipient_count"] >= 0
        assert data["quota_limit"] > 0
        assert data["remaining"] >= 0
        assert 0 <= data["usage_percentage"] <= 100

    @override_settings(MAX_RECIPIENTS_FOR_DOMAIN="5000/m")
    def test_maildomain_quota_as_superuser(self):
        """Test that a superuser can access maildomain quota."""
        user = factories.UserFactory(is_superuser=True)
        maildomain = factories.MailDomainFactory()

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse(
            "admin-maildomains-quota", kwargs={"maildomain_pk": maildomain.id}
        )
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK

    def test_maildomain_quota_unauthorized(self):
        """Test that users without access cannot view maildomain quota."""
        maildomain = factories.MailDomainFactory()
        user = factories.UserFactory()

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse(
            "admin-maildomains-quota", kwargs={"maildomain_pk": maildomain.id}
        )
        response = client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminMailboxQuotaEndpoint:
    """Test admin mailbox quota API endpoint (nested under maildomain)."""

    @override_settings(MAX_RECIPIENTS_FOR_MAILBOX="500/d")
    def test_admin_mailbox_quota(self):
        """Test domain admin can access mailbox quota via admin endpoint."""
        user = factories.UserFactory()
        maildomain = factories.MailDomainFactory()
        mailbox = factories.MailboxFactory(domain=maildomain)
        factories.MailDomainAccessFactory(
            maildomain=maildomain, user=user, role=MailDomainAccessRoleChoices.ADMIN
        )

        client = APIClient()
        client.force_authenticate(user=user)

        url = reverse(
            "admin-maildomains-mailbox-quota",
            kwargs={"maildomain_pk": maildomain.id, "pk": mailbox.id},
        )
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "period" in data
        assert "quota_limit" in data

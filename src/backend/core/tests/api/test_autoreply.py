"""API tests for autoreply template CRUD operations."""

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.tests.api.conftest import MESSAGE_TEMPLATE_RAW_DATA_JSON as RAW_DATA

pytestmark = pytest.mark.django_db


@pytest.fixture(name="user")
def fixture_user():
    """Create a test user."""
    return factories.UserFactory(full_name="Test User")


@pytest.fixture(name="mailbox")
def fixture_mailbox():
    """Create a test mailbox."""
    return factories.MailboxFactory()


@pytest.fixture(name="list_url")
def fixture_list_url(mailbox):
    """URL to list message templates for a mailbox."""
    return reverse(
        "mailbox-message-templates-list",
        kwargs={"mailbox_id": mailbox.id},
    )


@pytest.fixture(name="detail_url")
def fixture_detail_url(mailbox):
    """URL to get a message template detail for a mailbox."""
    return lambda template_id: reverse(
        "mailbox-message-templates-detail",
        kwargs={"mailbox_id": mailbox.id, "pk": template_id},
    )


def _autoreply_payload(schedule_type="always", **extra_metadata):
    """Build a standard autoreply creation payload."""
    metadata = {"schedule_type": schedule_type, **extra_metadata}
    return {
        "name": "Out of office",
        "type": "autoreply",
        "html_body": "<p>I am out of office.</p>",
        "text_body": "I am out of office.",
        "raw_body": RAW_DATA,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Create autoreply templates
# ---------------------------------------------------------------------------


class TestCreateAutoreplyTemplate:
    """Test creating autoreply templates via the API."""

    def test_create_always_schedule(self, user, mailbox, list_url):
        """Create autoreply with always schedule."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        response = client.post(list_url, _autoreply_payload(), format="json")
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["type"] == "autoreply"
        assert data["metadata"]["schedule_type"] == "always"

    def test_create_date_range_schedule(self, user, mailbox, list_url):
        """Create autoreply with date_range schedule."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(
            schedule_type="date_range",
            start_at="2026-03-01T00:00:00Z",
            end_at="2026-03-15T23:59:59Z",
        )
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["metadata"]["schedule_type"] == "date_range"

    def test_create_recurring_weekly_schedule(self, user, mailbox, list_url):
        """Create autoreply with recurring_weekly schedule."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(
            schedule_type="recurring_weekly",
            intervals=[
                {
                    "start_day": 5,
                    "start_time": "18:00",
                    "end_day": 1,
                    "end_time": "08:00",
                }
            ],
            timezone="Europe/Paris",
        )
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# Validate metadata schema
# ---------------------------------------------------------------------------


class TestAutoreplyMetadataValidation:
    """Test metadata validation for autoreply templates."""

    def _post(self, client, list_url, **overrides):
        payload = _autoreply_payload(**overrides)
        return client.post(list_url, payload, format="json")

    def test_missing_schedule_type(self, user, mailbox, list_url):
        """Reject empty metadata without schedule_type."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["metadata"] = {}
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_schedule_type(self, user, mailbox, list_url):
        """Reject unknown schedule_type value."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["metadata"] = {"schedule_type": "never"}
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_date_range_missing_dates(self, user, mailbox, list_url):
        """Reject date_range without start_at/end_at."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(schedule_type="date_range")
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_date_range_start_after_end(self, user, mailbox, list_url):
        """Reject date_range where start_at >= end_at."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(
            schedule_type="date_range",
            start_at="2026-03-15T00:00:00Z",
            end_at="2026-03-01T00:00:00Z",
        )
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_recurring_weekly_missing_intervals(self, user, mailbox, list_url):
        """Reject recurring_weekly without intervals."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(schedule_type="recurring_weekly")
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_recurring_weekly_invalid_day_values(self, user, mailbox, list_url):
        """Reject intervals with out-of-range day values."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(
            schedule_type="recurring_weekly",
            intervals=[
                {
                    "start_day": 0,
                    "start_time": "18:00",
                    "end_day": 8,
                    "end_time": "08:00",
                }
            ],
        )
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_recurring_weekly_invalid_time_values(self, user, mailbox, list_url):
        """Reject intervals with invalid time strings."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(
            schedule_type="recurring_weekly",
            intervals=[
                {
                    "start_day": 1,
                    "start_time": "invalid",
                    "end_day": 5,
                    "end_time": "08:00",
                }
            ],
        )
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_timezone(self, user, mailbox, list_url):
        """Reject invalid timezone string."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload(timezone="Invalid/Timezone")
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Autoreply type constraints
# ---------------------------------------------------------------------------


class TestAutoreplyConstraints:
    """Test autoreply-specific constraints."""

    def test_rejects_is_forced(self, user, mailbox, list_url):
        """Reject autoreply with is_forced=True."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["is_forced"] = True
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_is_default(self, user, mailbox, list_url):
        """Reject autoreply with is_default=True."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["is_default"] = True
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_only_one_active_autoreply_per_mailbox(self, user, mailbox, list_url):
        """Creating a second active autoreply should deactivate the first."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        client = APIClient()
        client.force_login(user)

        # Create first
        response1 = client.post(list_url, _autoreply_payload(), format="json")
        assert response1.status_code == status.HTTP_201_CREATED
        first_id = response1.json()["id"]

        # Create second
        payload2 = _autoreply_payload()
        payload2["name"] = "Vacation reply"
        response2 = client.post(list_url, payload2, format="json")
        assert response2.status_code == status.HTTP_201_CREATED

        # First should now be deactivated
        first = models.MessageTemplate.objects.get(id=first_id)
        assert first.is_active is False


# ---------------------------------------------------------------------------
# List/filter
# ---------------------------------------------------------------------------


class TestAutoreplyListFilter:
    """Test listing and filtering autoreply templates."""

    def test_filter_by_type(self, user, mailbox, list_url):
        """Filter templates by autoreply type."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        # Create one of each type
        factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            mailbox=mailbox,
        )
        factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )

        client = APIClient()
        client.force_login(user)

        response = client.get(f"{list_url}?type=autoreply")
        assert response.status_code == status.HTTP_200_OK
        results = response.json()
        if isinstance(results, dict):
            results = results.get("results", [])
        assert all(r["type"] == "autoreply" for r in results)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# Update and delete
# ---------------------------------------------------------------------------


class TestAutoreplyUpdateDelete:
    """Test update and delete operations on autoreply templates."""

    def test_update_metadata(self, user, mailbox, detail_url):
        """Update autoreply metadata via PATCH."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )
        client = APIClient()
        client.force_login(user)

        response = client.patch(
            detail_url(template.id),
            {
                "metadata": {
                    "schedule_type": "date_range",
                    "start_at": "2026-04-01T00:00:00Z",
                    "end_at": "2026-04-15T00:00:00Z",
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        template.refresh_from_db()
        assert template.metadata["schedule_type"] == "date_range"

    def test_disable_autoreply(self, user, mailbox, detail_url):
        """Disable autoreply via PATCH."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )
        client = APIClient()
        client.force_login(user)

        response = client.patch(
            detail_url(template.id),
            {"is_active": False},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        template.refresh_from_db()
        assert template.is_active is False

    def test_delete_autoreply(self, user, mailbox, detail_url):
        """Delete autoreply via DELETE."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )
        client = APIClient()
        client.force_login(user)

        response = client.delete(detail_url(template.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not models.MessageTemplate.objects.filter(id=template.id).exists()


# ---------------------------------------------------------------------------
# Signature validation on autoreply templates
# ---------------------------------------------------------------------------


class TestAutoreplySignatureValidation:
    """Test signature_id validation when creating/updating autoreply templates."""

    def test_create_with_valid_signature(self, user, mailbox, list_url):
        """Creating an autoreply with a valid mailbox signature succeeds."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        signature = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["signature_id"] = str(signature.id)
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["signature"] == str(signature.id)

    def test_create_with_domain_signature(self, user, mailbox, list_url):
        """Creating an autoreply with a domain-level signature succeeds."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        domain_sig = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            maildomain=mailbox.domain,
            is_active=True,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["signature_id"] = str(domain_sig.id)
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["signature"] == str(domain_sig.id)

    def test_reject_signature_wrong_type(self, user, mailbox, list_url):
        """Reject signature_id pointing to a MESSAGE template."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        message_tmpl = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            mailbox=mailbox,
            is_active=True,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["signature_id"] = str(message_tmpl.id)
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reject_inactive_signature(self, user, mailbox, list_url):
        """Reject signature_id of an inactive signature."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        inactive_sig = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=False,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["signature_id"] = str(inactive_sig.id)
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_reject_signature_other_mailbox(self, user, mailbox, list_url):
        """Reject signature_id belonging to a different mailbox."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        other_mailbox = factories.MailboxFactory()
        other_sig = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=other_mailbox,
            is_active=True,
        )
        client = APIClient()
        client.force_login(user)

        payload = _autoreply_payload()
        payload["signature_id"] = str(other_sig.id)
        response = client.post(list_url, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_set_signature_null(self, user, mailbox, detail_url):
        """PATCH signature_id=null removes the signature FK."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        signature = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
            signature=signature,
        )
        client = APIClient()
        client.force_login(user)

        response = client.patch(
            detail_url(template.id),
            {"signature_id": None},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        template.refresh_from_db()
        assert template.signature is None

    def test_update_change_signature(self, user, mailbox, detail_url):
        """PATCH with a new valid signature_id updates the FK."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        sig1 = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
        )
        sig2 = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
            signature=sig1,
        )
        client = APIClient()
        client.force_login(user)

        response = client.patch(
            detail_url(template.id),
            {"signature_id": str(sig2.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        template.refresh_from_db()
        assert template.signature_id == sig2.id


# ---------------------------------------------------------------------------
# Response fields: is_active_autoreply, metadata, signature
# ---------------------------------------------------------------------------


class TestAutoreplyResponseFields:
    """Test that autoreply responses include expected computed fields."""

    def test_is_active_autoreply_always(self, user, mailbox, detail_url):
        """GET autoreply with always schedule returns is_active_autoreply=true."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )
        client = APIClient()
        client.force_login(user)

        response = client.get(detail_url(template.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["is_active_autoreply"] is True

    def test_is_active_autoreply_expired(self, user, mailbox, detail_url):
        """GET autoreply with expired date_range returns is_active_autoreply=false."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={
                "schedule_type": "date_range",
                "start_at": "2020-01-01T00:00:00Z",
                "end_at": "2020-01-02T00:00:00Z",
            },
        )
        client = APIClient()
        client.force_login(user)

        response = client.get(detail_url(template.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["is_active_autoreply"] is False

    def test_is_active_autoreply_non_autoreply(self, user, mailbox, detail_url):
        """GET MESSAGE template returns is_active_autoreply=null."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.MESSAGE,
            mailbox=mailbox,
        )
        client = APIClient()
        client.force_login(user)

        response = client.get(detail_url(template.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["is_active_autoreply"] is None

    def test_response_contains_metadata(self, user, mailbox, detail_url):
        """GET autoreply response includes metadata with schedule_type."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )
        client = APIClient()
        client.force_login(user)

        response = client.get(detail_url(template.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "metadata" in data
        assert data["metadata"]["schedule_type"] == "always"

    def test_response_contains_signature_null(self, user, mailbox, detail_url):
        """GET autoreply without signature returns signature=null."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
        )
        client = APIClient()
        client.force_login(user)

        response = client.get(detail_url(template.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["signature"] is None

    def test_response_contains_signature_id(self, user, mailbox, detail_url):
        """GET autoreply with a linked signature returns its UUID."""
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=user,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        signature = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.SIGNATURE,
            mailbox=mailbox,
            is_active=True,
        )
        template = factories.MessageTemplateFactory(
            type=enums.MessageTemplateTypeChoices.AUTOREPLY,
            mailbox=mailbox,
            is_active=True,
            metadata={"schedule_type": "always"},
            signature=signature,
        )
        client = APIClient()
        client.force_login(user)

        response = client.get(detail_url(template.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["signature"] == str(signature.id)

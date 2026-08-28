"""Tests for the per-mailbox entitlements (storage quota) endpoint."""
# pylint: disable=redefined-outer-name

from django.test import override_settings
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import factories, models
from core.entitlements import EntitlementsUnavailableError
from core.entitlements.factory import get_entitlements_backend

pytestmark = pytest.mark.django_db


LOCAL_BACKEND = "core.entitlements.backends.local.LocalEntitlementsBackend"


@pytest.fixture(autouse=True)
def _reset_entitlements_backend():
    """The configured backend is a functools-cached singleton; drop it so each
    test's ``override_settings`` picks up fresh backend parameters."""
    get_entitlements_backend.cache_clear()
    yield
    get_entitlements_backend.cache_clear()


@pytest.fixture
def user():
    """A regular user."""
    return factories.UserFactory()


@pytest.fixture
def mailbox():
    """A mailbox."""
    return factories.MailboxFactory()


def url(mailbox):
    """Return the entitlements action URL for a mailbox."""
    return reverse("mailboxes-entitlements", kwargs={"pk": mailbox.id})


def test_requires_authentication(mailbox):
    """Anonymous users cannot read entitlements."""
    response = APIClient().get(url(mailbox))
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


def test_forbidden_without_mailbox_access(user, mailbox):
    """A user without access to the mailbox gets a 404 (queryset-filtered)."""
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(url(mailbox))
    assert response.status_code == status.HTTP_404_NOT_FOUND


@override_settings(
    ENTITLEMENTS_BACKEND=LOCAL_BACKEND,
    ENTITLEMENTS_BACKEND_PARAMETERS={"mailbox_storage_limit": 1000},
)
def test_returns_account_storage(user, mailbox):
    """A member sees usage and the configured limit for the mailbox."""
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(url(mailbox))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["account"]["max_storage"] == 1000
    assert data["account"]["storage_used"] == 0
    # No organization custom attribute set on the domain -> no org level.
    assert data["organization"] is None


@override_settings(
    ENTITLEMENTS_BACKEND=LOCAL_BACKEND,
    ENTITLEMENTS_BACKEND_PARAMETERS={"mailbox_storage_limit": 1000},
)
def test_storage_used_counts_messages(user, mailbox):
    """storage_used reflects message overhead for the mailbox's threads."""
    overhead = 1024
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
    )
    contact = factories.ContactFactory(mailbox=mailbox)
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
    factories.MessageFactory(thread=thread, sender=contact)
    factories.MessageFactory(thread=thread, sender=contact)

    client = APIClient()
    client.force_authenticate(user=user)

    with override_settings(METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE=overhead):
        response = client.get(url(mailbox))
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["account"]["storage_used"] == 2 * overhead


@override_settings(
    ENTITLEMENTS_BACKEND=LOCAL_BACKEND,
    ENTITLEMENTS_BACKEND_PARAMETERS={
        "mailbox_storage_limit": 1000,
        "organization_storage_limit": 5000,
        "organization_claim": "siret",
    },
)
def test_returns_organization_level_when_domain_has_org(user):
    """When the domain carries the org claim, an organization level is returned."""
    domain = factories.MailDomainFactory(custom_attributes={"siret": "12345678900001"})
    mailbox = factories.MailboxFactory(domain=domain)
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(url(mailbox))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["organization"] == {"storage_used": 0, "max_storage": 5000}


@override_settings(ENTITLEMENTS_BACKEND=LOCAL_BACKEND)
def test_degrades_when_backend_unavailable(user, mailbox, monkeypatch):
    """A backend outage hides the gauge (null limit) rather than erroring."""
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.VIEWER
    )

    def _raise(*_args, **_kwargs):
        raise EntitlementsUnavailableError("down")

    monkeypatch.setattr("core.api.viewsets.mailbox.get_mailbox_entitlements", _raise)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(url(mailbox))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data == {
        "account": {"storage_used": 0, "max_storage": None},
        "organization": None,
    }

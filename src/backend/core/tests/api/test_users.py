"""
Test users API endpoints in the messages core app.
"""

import pytest
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db


def test_api_users_retrieve_me_anonymous():
    """Anonymous users should not be allowed to list users."""
    factories.UserFactory.create_batch(2)
    client = APIClient()
    response = client.get("/api/v1.0/users/me/")
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


def test_api_users_retrieve_me_authenticated():
    """Authenticated users should be able to retrieve their own user via the "/users/me" path."""
    user = factories.UserFactory()

    client = APIClient()
    client.force_login(user)

    factories.UserFactory.create_batch(2)
    response = client.get(
        "/api/v1.0/users/me/",
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "abilities": {
            "create_maildomains": False,
            "view_maildomains": False,
        },
    }


def test_api_users_retrieve_me_with_abilities_regular_user():
    """Test abilities for regular user without mail domain access."""
    user = factories.UserFactory()

    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == 200
    data = response.json()
    abilities = data["abilities"]
    assert abilities["create_maildomains"] is False
    assert abilities["view_maildomains"] is False


def test_api_users_retrieve_me_with_abilities_user_with_access():
    """Test abilities for user with mail domain access."""
    user = factories.UserFactory()
    maildomain = factories.MailDomainFactory()

    # Give user access to a mail domain
    models.MailDomainAccess.objects.create(
        maildomain=maildomain,
        user=user,
        role=models.MailDomainAccessRoleChoices.ADMIN,
    )

    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == 200
    data = response.json()
    abilities = data["abilities"]
    assert abilities["create_maildomains"] is False
    assert abilities["view_maildomains"] is True


def test_api_users_retrieve_me_with_abilities_superuser_staff():
    """Test abilities for superuser and staff user."""
    user = factories.UserFactory(is_superuser=True, is_staff=True)

    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == 200
    data = response.json()
    abilities = data["abilities"]
    assert abilities["create_maildomains"] is True
    assert abilities["view_maildomains"] is True


def test_api_users_retrieve_me_with_abilities_superuser_not_staff():
    """Test abilities for superuser without staff status."""
    user = factories.UserFactory(is_superuser=True, is_staff=False)

    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == 200
    data = response.json()
    abilities = data["abilities"]
    assert abilities["create_maildomains"] is False
    assert abilities["view_maildomains"] is False


def test_api_users_retrieve_me_with_abilities_staff_not_superuser():
    """Test abilities for staff user without superuser status."""
    user = factories.UserFactory(is_superuser=False, is_staff=True)

    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == 200
    data = response.json()
    abilities = data["abilities"]
    assert abilities["create_maildomains"] is False
    assert abilities["view_maildomains"] is False


def test_api_users_retrieve_me_with_abilities_superuser_staff_with_access():
    """Test abilities for superuser/staff with mail domain access."""
    user = factories.UserFactory(is_superuser=True, is_staff=True)
    maildomain = factories.MailDomainFactory()

    # Give user access to a mail domain
    models.MailDomainAccess.objects.create(
        maildomain=maildomain,
        user=user,
        role=models.MailDomainAccessRoleChoices.ADMIN,
    )

    client = APIClient()
    client.force_login(user)

    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == 200
    data = response.json()
    abilities = data["abilities"]
    assert abilities["create_maildomains"] is True
    assert abilities["view_maildomains"] is True


def test_users_me_endpoint_includes_abilities_by_default():
    """Test that /users/me/ endpoint includes abilities by default (no exclude_abilities)."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v1.0/users/me/")
    assert response.status_code == 200

    data = response.json()
    # Verify that abilities ARE included by default
    assert "abilities" in data
    assert data["abilities"] == user.get_abilities()
    assert "id" in data
    assert "email" in data
    assert "full_name" in data

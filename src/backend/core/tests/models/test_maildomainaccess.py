from django.core.exceptions import ValidationError

import pytest

from core import factories, models
from core.enums import MailDomainAccessRoleChoices

pytestmark = pytest.mark.django_db


class TestMailDomainAccessModel:
    def test_create_mail_domain_access(self):
        user = factories.UserFactory()
        maildomain = factories.MailDomainFactory()

        access = models.MailDomainAccess.objects.create(
            user=user, maildomain=maildomain, role=MailDomainAccessRoleChoices.ADMIN
        )

        assert access.user == user
        assert access.maildomain == maildomain
        assert access.role == MailDomainAccessRoleChoices.ADMIN
        assert (
            str(access)
            == f"Access to {maildomain.name} for {user} with {MailDomainAccessRoleChoices.ADMIN.value} role"
        )

    def test_unique_together_constraint(self):
        user = factories.UserFactory()
        maildomain = factories.MailDomainFactory()

        models.MailDomainAccess.objects.create(
            user=user, maildomain=maildomain, role=MailDomainAccessRoleChoices.ADMIN
        )

        with pytest.raises(ValidationError):
            models.MailDomainAccess.objects.create(
                user=user,  # Same user
                maildomain=maildomain,  # Same maildomain
                role=MailDomainAccessRoleChoices.ADMIN,
            )

    def test_related_names(self):
        user = factories.UserFactory()
        maildomain = factories.MailDomainFactory()
        access = factories.MailDomainAccessFactory(user=user, maildomain=maildomain)

        assert user.maildomain_accesses.first() == access
        assert maildomain.accesses.first() == access

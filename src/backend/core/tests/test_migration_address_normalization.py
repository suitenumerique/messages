"""Tests for the data-fold half of migration 0035.

Rows are forced into their unfolded shape with ``queryset.update()``, which
bypasses ``save()``/``full_clean()`` — the only way to reproduce data written
before the models started folding.

The migration functions are called with the live app registry: their only
use of ``apps`` is ``get_model``, whose signature is the same either way,
and the fields they touch have not changed shape since 0035.
"""
# pylint: disable=redefined-outer-name,unused-argument

import importlib

from django.apps import apps

import pytest

from core import factories, models
from core.tests.mda.test_addresses import KELVIN_SIGN

# The module name starts with a digit, so it cannot be imported by statement.
migration = importlib.import_module("core.migrations.0035_address_normalization")

pytestmark = pytest.mark.django_db


def force(queryset, **values):
    """Write values straight to the DB, skipping model validation."""
    queryset.update(**values)


class TestFoldMailboxes:
    """Local parts are folded; collisions are left for an operator."""

    def test_local_part_is_folded(self):
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(local_part="placeholder", domain=domain)
        force(models.Mailbox.objects.filter(pk=mailbox.pk), local_part="John.DOE")

        migration.fold_mailboxes(apps, None)

        mailbox.refresh_from_db()
        assert mailbox.local_part == "john.doe"

    def test_identity_contact_follows_the_mailbox(self):
        """The mailbox's own contact is the From header of everything it sends."""
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(local_part="placeholder", domain=domain)
        contact = factories.ContactFactory(
            mailbox=mailbox, email="John.DOE@example.com"
        )
        mailbox.contact = contact
        mailbox.save()
        force(models.Mailbox.objects.filter(pk=mailbox.pk), local_part="John.DOE")

        migration.fold_mailboxes(apps, None)

        contact.refresh_from_db()
        assert contact.email == "john.doe@example.com"

    def test_identity_contact_repoints_to_an_existing_twin(self):
        """Contact has unique_together (email, mailbox): reuse, do not collide."""
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(local_part="placeholder", domain=domain)
        old = factories.ContactFactory(mailbox=mailbox, email="John.DOE@example.com")
        twin = factories.ContactFactory(mailbox=mailbox, email="john.doe@example.com")
        mailbox.contact = old
        mailbox.save()
        force(models.Mailbox.objects.filter(pk=mailbox.pk), local_part="John.DOE")

        migration.fold_mailboxes(apps, None)

        mailbox.refresh_from_db()
        assert mailbox.contact_id == twin.pk
        old.refresh_from_db()
        assert old.email == "John.DOE@example.com"

    def test_collision_is_left_untouched(self):
        """Merging two mailboxes moves threads and accesses: an operator's call."""
        domain = factories.MailDomainFactory(name="example.com")
        lower = factories.MailboxFactory(local_part="john.doe", domain=domain)
        upper = factories.MailboxFactory(local_part="placeholder", domain=domain)
        force(models.Mailbox.objects.filter(pk=upper.pk), local_part="John.Doe")

        migration.fold_mailboxes(apps, None)

        upper.refresh_from_db()
        lower.refresh_from_db()
        assert upper.local_part == "John.Doe"
        assert lower.local_part == "john.doe"

    def test_is_idempotent(self):
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(local_part="placeholder", domain=domain)
        force(models.Mailbox.objects.filter(pk=mailbox.pk), local_part="John.DOE")

        migration.fold_mailboxes(apps, None)
        migration.fold_mailboxes(apps, None)

        mailbox.refresh_from_db()
        assert mailbox.local_part == "john.doe"

    def test_non_ascii_local_part_is_left_alone(self):
        """ASCII-only folding: a look-alike must not be folded onto ASCII."""
        domain = factories.MailDomainFactory(name="example.com")
        mailbox = factories.MailboxFactory(local_part="placeholder", domain=domain)
        force(
            models.Mailbox.objects.filter(pk=mailbox.pk), local_part=f"nic{KELVIN_SIGN}"
        )

        migration.fold_mailboxes(apps, None)

        mailbox.refresh_from_db()
        assert mailbox.local_part == f"nic{KELVIN_SIGN}"


class TestFoldMailDomains:
    """Domain names are folded, collisions skipped."""

    def test_name_is_folded(self):
        domain = factories.MailDomainFactory(name="placeholder.example")
        force(models.MailDomain.objects.filter(pk=domain.pk), name="EXAMPLE.COM")

        migration.fold_maildomains(apps, None)

        domain.refresh_from_db()
        assert domain.name == "example.com"

    def test_collision_is_left_untouched(self):
        lower = factories.MailDomainFactory(name="example.com")
        upper = factories.MailDomainFactory(name="placeholder.example")
        force(models.MailDomain.objects.filter(pk=upper.pk), name="EXAMPLE.COM")

        migration.fold_maildomains(apps, None)

        upper.refresh_from_db()
        assert upper.name == "EXAMPLE.COM"
        lower.refresh_from_db()
        assert lower.name == "example.com"


class TestFoldUsers:
    """Both identity addresses are folded; unique admin_email can collide."""

    def test_email_is_folded(self):
        user = factories.UserFactory(email="placeholder@example.com")
        force(models.User.objects.filter(pk=user.pk), email="John.DOE@EXAMPLE.COM")

        migration.fold_users(apps, None)

        user.refresh_from_db()
        assert user.email == "john.doe@example.com"

    def test_duplicate_emails_are_both_folded(self):
        """User.email is not unique, so there is nothing to skip."""
        first = factories.UserFactory(email="a@example.com")
        second = factories.UserFactory(email="b@example.com")
        force(models.User.objects.filter(pk=first.pk), email="Dup@Example.com")
        force(models.User.objects.filter(pk=second.pk), email="DUP@example.com")

        migration.fold_users(apps, None)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.email == "dup@example.com"
        assert second.email == "dup@example.com"

    def test_admin_email_is_folded(self):
        user = factories.UserFactory(admin_email="placeholder@example.com")
        force(
            models.User.objects.filter(pk=user.pk),
            admin_email="Admin@EXAMPLE.COM",
        )

        migration.fold_users(apps, None)

        user.refresh_from_db()
        assert user.admin_email == "admin@example.com"

    def test_admin_email_collision_is_left_untouched(self):
        lower = factories.UserFactory(admin_email="admin@example.com")
        upper = factories.UserFactory(admin_email="placeholder@example.com")
        force(
            models.User.objects.filter(pk=upper.pk),
            admin_email="Admin@example.com",
        )

        migration.fold_users(apps, None)

        upper.refresh_from_db()
        assert upper.admin_email == "Admin@example.com"
        lower.refresh_from_db()
        assert lower.admin_email == "admin@example.com"

    def test_non_ascii_email_is_left_alone(self):
        user = factories.UserFactory(email="placeholder@example.com")
        force(
            models.User.objects.filter(pk=user.pk),
            email=f"nic{KELVIN_SIGN}@example.com",
        )

        migration.fold_users(apps, None)

        user.refresh_from_db()
        assert user.email == f"nic{KELVIN_SIGN}@example.com"

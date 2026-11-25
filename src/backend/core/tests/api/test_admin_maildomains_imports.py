"""Tests for the imports of a mail domain, as its admins see and drive them.

``GET /maildomains/{id}/imports/`` lists the runs of every mailbox of the
domain; starting or acting on a run goes through ``/mailboxes/{id}/imports/``.
"""

# pylint: disable=redefined-outer-name

from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import enums, factories, models
from core.services.importer.channel import (
    create_import_channel,
    enable_continuous,
    mark_finished,
    mark_started,
    pause_import,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def domain():
    """The administered mail domain."""
    return factories.MailDomainFactory()


@pytest.fixture
def domain_admin(domain):
    """An admin of ``domain`` with no access to any of its mailboxes."""
    user = factories.UserFactory()
    factories.MailDomainAccessFactory(
        maildomain=domain, user=user, role=enums.MailDomainAccessRoleChoices.ADMIN
    )
    return user


@pytest.fixture
def api_client(domain_admin):
    """A client authenticated as the domain admin."""
    client = APIClient()
    client.force_authenticate(user=domain_admin)
    return client


def _list_url(domain):
    return reverse(
        "admin-maildomains-imports-list", kwargs={"maildomain_pk": domain.id}
    )


def _make_import(mailbox, user=None, source=enums.ImportSource.MBOX):
    return create_import_channel(recipient=mailbox, user=user, source_type=source.value)


def _finish(channel, status_value):
    mark_finished(channel, status=status_value, success=1, failure=0, total=1)


def _ids(response):
    return {row["id"] for row in response.data["results"]}


class TestAdminMailDomainImportsPermissions:
    """Who may list the imports of a domain."""

    def test_domain_admin_lists_the_imports_of_every_domain_mailbox(
        self, api_client, domain
    ):
        """The admin sees runs started by anyone, in mailboxes they cannot
        access themselves."""
        first = _make_import(factories.MailboxFactory(domain=domain))
        second = _make_import(
            factories.MailboxFactory(domain=domain), user=factories.UserFactory()
        )

        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response) == {str(first.id), str(second.id)}

    def test_imports_of_other_domains_are_not_listed(self, api_client, domain):
        mine = _make_import(factories.MailboxFactory(domain=domain))
        _make_import(factories.MailboxFactory())

        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response) == {str(mine.id)}

    def test_superuser_lists_the_imports_of_any_domain(self, domain):
        run = _make_import(factories.MailboxFactory(domain=domain))
        client = APIClient()
        client.force_authenticate(user=factories.UserFactory(is_superuser=True))

        response = client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response) == {str(run.id)}

    def test_mailbox_admin_who_is_not_domain_admin_is_denied(self, domain):
        """Administering one mailbox of the domain does not open the whole
        domain's imports."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory(domain=domain, users_admin=[user])
        _make_import(mailbox, user=user)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(_list_url(domain))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_of_another_domain_is_denied(self, domain):
        user = factories.UserFactory()
        factories.MailDomainAccessFactory(
            user=user, role=enums.MailDomainAccessRoleChoices.ADMIN
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(_list_url(domain))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_anonymous_is_denied(self, domain):
        response = APIClient().get(_list_url(domain))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_denied_when_imports_are_disabled(self, api_client, domain, settings):
        settings.FEATURE_IMPORT_MESSAGES = False

        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminMailDomainImportsFilters:
    """The ``status`` and ``q`` filters, and the pagination."""

    @pytest.fixture
    def runs(self, domain):
        """One import of the domain in each state the list tells apart."""
        mailbox = factories.MailboxFactory(domain=domain)

        pending = _make_import(mailbox)

        running = _make_import(mailbox)
        mark_started(running.id, total=10)

        failed = _make_import(mailbox)
        _finish(failed, enums.ImportStatus.FAILED.value)

        completed = _make_import(mailbox)
        _finish(completed, enums.ImportStatus.COMPLETED.value)

        polling = _make_import(mailbox, source=enums.ImportSource.IMAP)
        enable_continuous(polling)
        _finish(polling, enums.ImportStatus.COMPLETED.value)

        paused = _make_import(mailbox, source=enums.ImportSource.IMAP)
        enable_continuous(paused)
        _finish(paused, enums.ImportStatus.COMPLETED.value)
        pause_import(paused)

        # Resumed after a failed pass: active again, still failed until the
        # next pass ends.
        resumed = _make_import(mailbox, source=enums.ImportSource.IMAP)
        enable_continuous(resumed)
        _finish(resumed, enums.ImportStatus.FAILED.value)
        enable_continuous(resumed)

        cancelled = _make_import(mailbox)
        _finish(cancelled, enums.ImportStatus.CANCELLED.value)

        return {
            name: str(channel.id)
            for name, channel in {
                "pending": pending,
                "running": running,
                "failed": failed,
                "completed": completed,
                "polling": polling,
                "paused": paused,
                "resumed": resumed,
                "cancelled": cancelled,
            }.items()
        }

    def test_default_lists_every_import_but_cancelled_ones(
        self, api_client, domain, runs
    ):
        """A cancelled run is being purged: it is gone for the admin already."""
        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response) == set(runs.values()) - {runs["cancelled"]}

    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            (["in_progress"], {"pending", "running", "polling"}),
            (["failed"], {"failed", "resumed"}),
            (["completed"], {"completed", "paused"}),
            (
                ["in_progress", "failed"],
                {"pending", "running", "polling", "failed", "resumed"},
            ),
        ],
    )
    def test_status_keeps_the_imports_in_any_of_the_given_statuses(
        self, api_client, domain, runs, statuses, expected
    ):
        response = api_client.get(_list_url(domain), {"status": statuses})

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response) == {runs[name] for name in expected}

    def test_every_listed_import_has_exactly_one_status(self, api_client, domain, runs):
        by_status = [
            _ids(api_client.get(_list_url(domain), {"status": name}))
            for name in ("in_progress", "failed", "completed")
        ]

        listed = _ids(api_client.get(_list_url(domain)))
        assert sorted(run for ids in by_status for run in ids) == sorted(listed)

    def test_unknown_status_is_rejected(self, api_client, domain):
        response = api_client.get(
            _list_url(domain), {"status": ["completed", "active"]}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "status" in response.data

    def test_search_matches_the_local_part_or_the_contact_name(
        self, api_client, domain
    ):
        by_address = _make_import(
            factories.MailboxFactory(domain=domain, local_part="compta")
        )
        named = factories.MailboxFactory(domain=domain, local_part="j.martin")
        named.contact = factories.ContactFactory(
            mailbox=named, email=str(named), name="Jeanne Comptable"
        )
        named.save()
        by_name = _make_import(named)
        _make_import(factories.MailboxFactory(domain=domain, local_part="rh"))

        response = api_client.get(_list_url(domain), {"q": "compt"})

        assert response.status_code == status.HTTP_200_OK
        assert _ids(response) == {str(by_address.id), str(by_name.id)}

    def test_list_is_paginated(self, api_client, domain):
        """Unlike a mailbox's own imports, a domain's can be many."""
        mailbox = factories.MailboxFactory(domain=domain)
        for _ in range(21):
            _make_import(mailbox)

        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 21
        assert len(response.data["results"]) == 20
        assert response.data["next"] is not None


class TestAdminMailDomainImportsSerialization:
    """What a row of the domain list tells about a run."""

    def test_row_names_the_mailbox_and_who_started_the_import(self, api_client, domain):
        mailbox = factories.MailboxFactory(domain=domain)
        launcher = factories.UserFactory(full_name="Ada Admin")
        run = _make_import(mailbox, user=launcher)

        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        [row] = response.data["results"]
        assert row["id"] == str(run.id)
        # The bare id, to act on the run through its mailbox route.
        assert str(row["mailbox"]) == str(mailbox.id)
        assert row["mailbox_details"]["id"] == str(mailbox.id)
        assert row["mailbox_details"]["email"] == str(mailbox)
        assert row["created_by"]["id"] == str(launcher.id)
        assert row["created_by"]["full_name"] == "Ada Admin"
        assert row["status"] == enums.ImportStatus.PENDING.value

    def test_created_by_is_null_once_the_launcher_is_deleted(self, api_client, domain):
        launcher = factories.UserFactory()
        _make_import(factories.MailboxFactory(domain=domain), user=launcher)
        launcher.delete()

        response = api_client.get(_list_url(domain))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"][0]["created_by"] is None

    def test_query_count_does_not_grow_with_the_rows(self, api_client, domain):
        """Mailbox, domain, contact and launcher come with the runs."""
        _make_import(factories.MailboxFactory(domain=domain), factories.UserFactory())
        with CaptureQueriesContext(connection) as one_row:
            api_client.get(_list_url(domain))

        for _ in range(3):
            _make_import(
                factories.MailboxFactory(domain=domain), factories.UserFactory()
            )
        with CaptureQueriesContext(connection) as four_rows:
            response = api_client.get(_list_url(domain))

        assert response.data["count"] == 4
        assert len(four_rows.captured_queries) == len(one_row.captured_queries)


class TestDomainAdminActsThroughTheMailboxRoute:
    """A domain admin starts and manages imports with the mailbox-nested
    routes, without any access to the mailbox."""

    def test_domain_admin_starts_an_import_in_a_domain_mailbox(
        self, api_client, domain, domain_admin
    ):
        mailbox = factories.MailboxFactory(domain=domain)
        assert not mailbox.accesses.filter(user=domain_admin).exists()

        with patch("core.services.importer.service.run_import_task") as mock_task:
            response = api_client.post(
                reverse("mailbox-imports-list", kwargs={"mailbox_id": mailbox.id}),
                {
                    "source": "imap",
                    "imap_server": "imap.example.com",
                    "imap_port": 993,
                    "username": "user@example.com",
                    "password": "secret",
                    "use_ssl": True,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_task.delay.assert_called_once()
        channel = models.Channel.objects.get(id=response.data["id"])
        assert channel.mailbox == mailbox
        assert channel.user == domain_admin
        listed = api_client.get(_list_url(domain))
        assert _ids(listed) == {str(channel.id)}

    def test_domain_admin_cancels_an_import_of_a_domain_mailbox(
        self, api_client, domain
    ):
        mailbox = factories.MailboxFactory(domain=domain)
        run = _make_import(mailbox, user=factories.UserFactory())

        with patch("core.api.viewsets.imports.cancel_import_task") as mock_task:
            response = api_client.post(
                reverse(
                    "mailbox-imports-cancel",
                    kwargs={"mailbox_id": mailbox.id, "pk": run.id},
                )
            )

        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_task.delay.assert_called_once_with(str(run.id))

    def test_domain_admin_cannot_import_into_another_domain(self, api_client):
        other_mailbox = factories.MailboxFactory()

        with patch("core.services.importer.service.run_import_task") as mock_task:
            response = api_client.post(
                reverse(
                    "mailbox-imports-list", kwargs={"mailbox_id": other_mailbox.id}
                ),
                {
                    "source": "imap",
                    "imap_server": "imap.example.com",
                    "imap_port": 993,
                    "username": "user@example.com",
                    "password": "secret",
                    "use_ssl": True,
                },
                format="json",
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        mock_task.delay.assert_not_called()

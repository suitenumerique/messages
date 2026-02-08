"""Tests for the Maildomain users metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument, too-many-lines

from django.urls import reverse
from django.utils import timezone

import pytest

from core.enums import MessageTemplateTypeChoices
from core.factories import (
    AttachmentFactory,
    BlobFactory,
    ContactFactory,
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
    MessageFactory,
    MessageTemplateFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)
from core.models import MailboxAccess, MailDomain


def check_results_for_key(
    results: dict | list,
    expected: dict[str, int],
    group_key: str,
    group_value: str,
):
    """
    Assert that the metrics in results match expected values, optionally for a specific custom attribute group.
    """

    if not isinstance(results, list):
        raise ValueError(
            "When key is provided, results must be a list of dictionaries."
        )

    for result in results:
        if result[group_key] == group_value:
            for expected_key, expected_value in expected.items():
                if expected_value > 0:
                    assert expected_key in result["metrics"], (
                        f"Missing key: {expected_key} in result with key: {group_key}"
                    )
                    assert result["metrics"][expected_key] == expected_value
                else:
                    assert not result["metrics"].get(expected_key)
            return
    raise KeyError(f"No result found with key: {group_key} {group_value}")


@pytest.fixture
def url():
    """
    Returns the URL for the maildomain users metrics endpoint.
    """
    return reverse("maildomain-users-metrics")


@pytest.fixture
def url_with_siret_query_param(url):
    """
    Returns the metrics endpoint URL with the SIRET query parameter.
    """
    return f"{url}?group_by_maildomain_custom_attribute=siret"


@pytest.fixture
def correctly_configured_header(settings):
    """
    Returns the authentication header for the metrics endpoint.
    """
    return {"HTTP_AUTHORIZATION": f"Bearer {settings.METRICS_API_KEY}"}


def grant_access_to_mailbox_accessed_at(mailbox, user, accessed_at: timezone = None):
    """Grant access to a mailbox for a user, optionally setting accessed_at."""
    mba = MailboxAccessFactory(mailbox=mailbox, user=user)
    if accessed_at:
        mba.accessed_at = accessed_at
        mba.save()
    return mba


# config example
# [{
#   "siret" : "12345678901234",
#   "mailboxes": [
#       {"users": [
#           {"user": user1, "accessed_at": timezone.now() - timezone.timedelta(days=10)}]},
#           {"user": user2, "accessed_at": timezone.now() - timezone.timedelta(days=1)},
#       ],
#       {"users": []},
#   ]
# }]
def create_models_from_config(config, maildomain=None) -> list[MailboxAccess]:
    """Create maildomains, mailboxes, and accesses from a config structure."""
    accesses = []
    for domain_config in config:
        if maildomain:
            domain = maildomain
        elif "siret" in domain_config:
            domain = MailDomainFactory(
                custom_attributes={"siret": domain_config["siret"]}
            )
        elif "name" in domain_config:
            domain = MailDomainFactory(name=domain_config["name"])
        else:
            domain = MailDomainFactory()
        for mailbox_config in domain_config["mailboxes"]:
            mailbox = MailboxFactory(domain=domain)
            for user_config in mailbox_config["users"]:
                user = user_config["user"]
                accessed_at = user_config.get("accessed_at")
                accesses.append(
                    grant_access_to_mailbox_accessed_at(mailbox, user, accessed_at)
                )
    return accesses


class TestMailDomainUsersMetrics:
    """
    Tests for the maildomain users metrics endpoint.
    """

    @pytest.mark.django_db
    def test_metrics_endpoint_requires_auth(
        self, api_client, url, correctly_configured_header
    ):
        """
        Requires valid API key for access.

        Asserts that requests without or with invalid authentication are rejected (403),
        and requests with the correct API key are accepted (200).
        """
        # Test without authentication
        response = api_client.get(url)
        assert response.status_code == 403

        # Test with invalid authentication
        response = api_client.get(url, HTTP_AUTHORIZATION="Bearer invalid_token")
        assert response.status_code == 403

        # Test with authentication
        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_no_group_no_users(self, api_client, url, correctly_configured_header):
        """
        Returns zero stats when no users exist.

        Asserts that the response contains overall user and mailbox counts.
        """

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        assert response.json() == {"count": 0, "results": []}

    @pytest.mark.django_db
    def test_no_group_users_no_access(
        self, api_client, url, correctly_configured_header
    ):
        """
        Returns zero active users if users never accessed mailboxes.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        # Create a specific domain
        domain = MailDomainFactory(name="example.com")

        # Create mailbox accesses for users with the specific domain
        MailboxAccessFactory.create_batch(3, mailbox__domain=domain)
        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 3,  # Total unique users
                "yau": 0,  # Yearly active users
                "mau": 0,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="domain",
            group_value="example.com",
        )

    @pytest.mark.django_db
    def test_no_group_users_access(self, api_client, url, correctly_configured_header):
        """
        Returns all users as active if all accessed recently.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        # Create a specific domain
        domain = MailDomainFactory(name="example.com")

        # Create mailbox accesses for users with the specific domain
        mas = MailboxAccessFactory.create_batch(3, mailbox__domain=domain)
        for ma in mas:
            ma.accessed_at = timezone.now()
            ma.save()
        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 3,  # Total unique users
                "yau": 3,  # Yearly active users
                "mau": 3,  # Monthly active users
                "wau": 3,  # Weekly active users
            },
            group_key="domain",
            group_value="example.com",
        )

    @pytest.mark.django_db
    def test_no_group_users_old_access(
        self, api_client, url, correctly_configured_header
    ):
        """
        Correctly counts users by last access time.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        domain = MailDomainFactory(name="example.com")

        create_models_from_config(
            [
                {
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory()
                                }  # Never accessed, only counted in tu
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=400),
                                }  # Old, only counted in tu
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=40),
                                }  # Only counted in tu + yau
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=10),
                                }  # Only counted in tu + yau + mau
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=1),
                                }  # Counted in tu + yau + mau + wau
                            ]
                        },
                    ],
                }
            ],
            maildomain=domain,
        )

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 5,  # Total unique users
                "yau": 3,  # Yearly active users
                "mau": 2,  # Monthly active users
                "wau": 1,  # Weekly active users
            },
            group_key="domain",
            group_value="example.com",
        )

    @pytest.mark.django_db
    def test_group_no_data(
        self, api_client, url_with_siret_query_param, correctly_configured_header
    ):
        """
        Returns no results when grouping and no data exists.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        # Create mailbox accesses for users
        response = api_client.get(
            url_with_siret_query_param,
            **correctly_configured_header,
        )
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert response.json()["results"] == []

    @pytest.mark.django_db
    def test_group_one_access(
        self, api_client, url_with_siret_query_param, correctly_configured_header
    ):
        """
        Groups stats for one user with no access.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        create_models_from_config(
            [
                {
                    "siret": "12345678901234",
                    "mailboxes": [
                        {"users": [{"user": UserFactory()}]},
                    ],
                }
            ]
        )

        response = api_client.get(
            url_with_siret_query_param,
            **correctly_configured_header,
        )
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 0,  # Yearly active users
                "mau": 0,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value="12345678901234",
        )

    @pytest.mark.django_db
    def test_group_multi_access_one_domain_one_user(
        self, api_client, url, correctly_configured_header
    ):
        """
        Groups stats for one user with two mailboxes in one domain.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        user = UserFactory()

        mba = create_models_from_config(
            [
                {
                    "siret": "12345678901234",
                    "mailboxes": [
                        {"users": [{"user": user}]},
                        {"users": [{"user": user}]},
                    ],
                }
            ]
        )

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 0,  # Yearly active users
                "mau": 0,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value="12345678901234",
        )
        mba[0].accessed_at = timezone.now() - timezone.timedelta(days=10)
        mba[0].save()
        mba[1].accessed_at = timezone.now() - timezone.timedelta(days=1)
        mba[1].save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 1,  # Yearly active users
                "mau": 1,  # Monthly active users
                "wau": 1,  # Weekly active users
            },
            group_key="siret",
            group_value="12345678901234",
        )

    @pytest.mark.django_db
    def test_group_multi_access_multi_domain_one_user(
        self, api_client, url, correctly_configured_header
    ):
        """
        Groups stats for one user with mailboxes in two domains.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret1 = "12345678901234"
        siret2 = "12345678909876"

        user = UserFactory()

        create_models_from_config(
            [
                {
                    "siret": siret1,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": user,
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=364),
                                }
                            ]
                        }
                    ],
                },
                {
                    "siret": siret2,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": user,
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=29),
                                }
                            ]
                        }
                    ],
                },
            ]
        )

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 2
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 1,  # Yearly active users
                "mau": 0,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value=siret1,
        )

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 1,  # Yearly active users
                "mau": 1,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value=siret2,
        )

    @pytest.mark.django_db
    def test_group_multi_access_one_domain_one_mailbox_multi_users(
        self, api_client, url, correctly_configured_header
    ):
        """
        Groups stats for two users with access to the same mailbox in one domain.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config(
            [
                {
                    "siret": siret,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=363),
                                },
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=1),
                                },
                            ]
                        },
                    ],
                }
            ]
        )

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 2,  # Total unique users
                "yau": 2,  # Yearly active users
                "mau": 1,  # Monthly active users
                "wau": 1,  # Weekly active users
            },
            group_key="siret",
            group_value=siret,
        )

    @pytest.mark.django_db
    def test_group_multi_access_one_domain_multi_mailbox_multi_users(
        self, api_client, url, correctly_configured_header
    ):
        """
        Groups stats for five users and three mailboxes in one domain.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config(
            [
                {
                    "siret": siret,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=363),
                                },
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=0),
                                },
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=29),
                                },
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=5),
                                },
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=366),
                                },
                            ]
                        },
                    ],
                }
            ]
        )

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 5,  # Total unique users
                "yau": 4,  # Yearly active users
                "mau": 3,  # Monthly active users
                "wau": 2,  # Weekly active users
            },
            group_key="siret",
            group_value=siret,
        )

    @pytest.mark.django_db
    def test_group_just_before_cutoff(
        self, api_client, url, correctly_configured_header
    ):
        """
        Groups stats for users accessed just before yearly, monthly, weekly cutoffs.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config(
            [
                {
                    "siret": siret,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(
                                        days=364, hours=23, minutes=59, seconds=59
                                    ),
                                },
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(
                                        days=29, hours=23, minutes=59, seconds=59
                                    ),
                                },
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(
                                        days=6, hours=23, minutes=59, seconds=59
                                    ),
                                },
                            ]
                        },
                    ],
                }
            ]
        )

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 3,  # Total unique users
                "yau": 3,  # Yearly active users
                "mau": 2,  # Monthly active users
                "wau": 1,  # Weekly active users
            },
            group_key="siret",
            group_value=siret,
        )

    @pytest.mark.django_db
    def test_group_exact_cutoff(self, api_client, url, correctly_configured_header):
        """
        Groups stats for users accessed exactly at yearly, monthly, weekly cutoffs.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config(
            [
                {
                    "siret": siret,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=365),
                                },
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=30),
                                },
                            ]
                        },
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=7),
                                },
                            ]
                        },
                    ],
                }
            ]
        )

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 3,  # Total unique users
                "yau": 2,  # Yearly active users
                "mau": 1,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value=siret,
        )

    @pytest.mark.django_db
    def test_group_missing_custom_attr(
        self, api_client, url, correctly_configured_header
    ):
        """
        Domains missing the custom attribute are not included in grouped results.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config(
            [
                {
                    "siret": siret,
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=150),
                                },
                            ]
                        },
                    ],
                },
                {
                    "mailboxes": [
                        {
                            "users": [
                                {
                                    "user": UserFactory(),
                                    "accessed_at": timezone.now()
                                    - timezone.timedelta(days=15),
                                },
                            ]
                        },
                    ],
                },
            ]
        )

        assert MailDomain.objects.count() == 2
        assert MailDomain.objects.filter(custom_attributes__siret=siret).count() == 1

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 2
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 1,  # Yearly active users
                "mau": 0,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value=siret,
        )

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 1,  # Yearly active users
                "mau": 1,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            group_key="siret",
            group_value=None,
        )


class TestMailDomainStorageUsedMetrics:
    """Tests for storage_used in the maildomain users metrics endpoint."""

    @pytest.mark.django_db
    def test_no_storage(self, api_client, url, correctly_configured_header):
        """Domain with users but no messages has storage_used zero."""
        domain = MailDomainFactory(name="example.com")
        MailboxAccessFactory(mailbox__domain=domain)

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        result = response.json()["results"][0]
        assert result["domain"] == "example.com"
        assert result["metrics"]["storage_used"] == 0

    @pytest.mark.django_db
    def test_basic_storage(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Domain storage counts messages and MIME blobs."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        domain = MailDomainFactory(name="example.com")
        mailbox = MailboxFactory(domain=domain)
        # Need a MailboxAccess so the domain appears in results
        MailboxAccessFactory(mailbox=mailbox)

        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)
        msg = MessageFactory(thread=thread, sender=contact, raw_mime=b"mime" * 100)

        response = api_client.get(url, **correctly_configured_header)
        result = response.json()["results"][0]

        assert result["metrics"]["storage_used"] == (
            1 * overhead + msg.blob.size_compressed
        )

    @pytest.mark.django_db
    def test_shared_thread_counted_once(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Two mailboxes in the same domain sharing a thread: messages counted once."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        domain = MailDomainFactory(name="example.com")
        mailbox_a = MailboxFactory(domain=domain)
        mailbox_b = MailboxFactory(domain=domain)
        MailboxAccessFactory(mailbox=mailbox_a)
        MailboxAccessFactory(mailbox=mailbox_b)

        # Shared thread with 2 messages
        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=thread)
        ThreadAccessFactory(mailbox=mailbox_b, thread=thread)
        contact = ContactFactory(mailbox=mailbox_a)
        msg1 = MessageFactory(thread=thread, sender=contact, raw_mime=b"msg1" * 100)
        msg2 = MessageFactory(thread=thread, sender=contact, raw_mime=b"msg2" * 100)

        response = api_client.get(url, **correctly_configured_header)
        result = response.json()["results"][0]

        # Messages and blobs counted once, not doubled
        assert result["metrics"]["storage_used"] == (
            2 * overhead + msg1.blob.size_compressed + msg2.blob.size_compressed
        )

    @pytest.mark.django_db
    def test_separate_threads_summed(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Two mailboxes in the same domain with separate threads: both counted."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        domain = MailDomainFactory(name="example.com")
        mailbox_a = MailboxFactory(domain=domain)
        mailbox_b = MailboxFactory(domain=domain)
        MailboxAccessFactory(mailbox=mailbox_a)
        MailboxAccessFactory(mailbox=mailbox_b)

        # mailbox_a's thread
        thread_a = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=thread_a)
        contact_a = ContactFactory(mailbox=mailbox_a)
        msg_a = MessageFactory(thread=thread_a, sender=contact_a, raw_mime=b"a" * 200)

        # mailbox_b's thread
        thread_b = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_b, thread=thread_b)
        contact_b = ContactFactory(mailbox=mailbox_b)
        msg_b = MessageFactory(thread=thread_b, sender=contact_b, raw_mime=b"b" * 300)

        response = api_client.get(url, **correctly_configured_header)
        result = response.json()["results"][0]

        assert result["metrics"]["storage_used"] == (
            2 * overhead + msg_a.blob.size_compressed + msg_b.blob.size_compressed
        )

    @pytest.mark.django_db
    def test_two_domains_independent(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Storage is computed independently per domain."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE

        domain_a = MailDomainFactory(name="a.com")
        domain_b = MailDomainFactory(name="b.com")
        mailbox_a = MailboxFactory(domain=domain_a)
        mailbox_b = MailboxFactory(domain=domain_b)
        MailboxAccessFactory(mailbox=mailbox_a)
        MailboxAccessFactory(mailbox=mailbox_b)

        thread_a = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_a, thread=thread_a)
        contact_a = ContactFactory(mailbox=mailbox_a)
        msg_a = MessageFactory(thread=thread_a, sender=contact_a, raw_mime=b"aa" * 100)

        thread_b = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox_b, thread=thread_b)
        contact_b = ContactFactory(mailbox=mailbox_b)
        MessageFactory(thread=thread_b, sender=contact_b)
        MessageFactory(thread=thread_b, sender=contact_b)

        response = api_client.get(url, **correctly_configured_header)
        results = response.json()["results"]

        by_domain = {r["domain"]: r for r in results}
        assert by_domain["a.com"]["metrics"]["storage_used"] == (
            1 * overhead + msg_a.blob.size_compressed
        )
        assert by_domain["b.com"]["metrics"]["storage_used"] == 2 * overhead

    @pytest.mark.django_db
    def test_full_formula_with_attachments(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Storage includes MIME blobs, draft blobs, and attachment blobs."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        domain = MailDomainFactory(name="example.com")
        mailbox = MailboxFactory(domain=domain)
        MailboxAccessFactory(mailbox=mailbox)

        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)

        # Message with MIME blob
        msg = MessageFactory(thread=thread, sender=contact, raw_mime=b"mime" * 100)

        # Draft with draft_blob
        draft_blob = BlobFactory(mailbox=mailbox, content=b"draft" * 50)
        draft = MessageFactory(
            thread=thread,
            sender=contact,
            is_draft=True,
            draft_blob=draft_blob,
        )

        # Attachment on the draft
        att = AttachmentFactory(mailbox=mailbox, blob_size=800)
        att.messages.add(draft)

        expected = (
            2 * overhead
            + msg.blob.size_compressed
            + draft_blob.size_compressed
            + att.blob.size_compressed
        )

        response = api_client.get(url, **correctly_configured_header)
        result = response.json()["results"][0]

        assert result["metrics"]["storage_used"] == expected

    @pytest.mark.django_db
    def test_storage_includes_template_blobs(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Domain signature/template blobs are counted toward storage."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        domain = MailDomainFactory(name="example.com")
        mailbox = MailboxFactory(domain=domain)
        MailboxAccessFactory(mailbox=mailbox)

        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)
        msg = MessageFactory(thread=thread, sender=contact, raw_mime=b"mime" * 100)

        # Domain-level signature template
        sig = MessageTemplateFactory(
            maildomain=domain,
            mailbox=None,
            type=MessageTemplateTypeChoices.SIGNATURE,
        )

        expected = 1 * overhead + msg.blob.size_compressed + sig.blob.size_compressed

        response = api_client.get(url, **correctly_configured_header)
        result = response.json()["results"][0]

        assert result["metrics"]["storage_used"] == expected

    @pytest.mark.django_db
    def test_storage_with_custom_attribute_grouping(
        self,
        api_client,
        url_with_siret_query_param,
        correctly_configured_header,
        settings,
    ):
        """Storage is grouped by custom attribute, summing across domains."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        siret = "12345678901234"

        # Two domains with the same siret
        domain1 = MailDomainFactory(custom_attributes={"siret": siret})
        domain2 = MailDomainFactory(custom_attributes={"siret": siret})
        mailbox1 = MailboxFactory(domain=domain1)
        mailbox2 = MailboxFactory(domain=domain2)
        MailboxAccessFactory(mailbox=mailbox1)
        MailboxAccessFactory(mailbox=mailbox2)

        thread1 = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox1, thread=thread1)
        contact1 = ContactFactory(mailbox=mailbox1)
        MessageFactory(thread=thread1, sender=contact1)

        thread2 = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox2, thread=thread2)
        contact2 = ContactFactory(mailbox=mailbox2)
        MessageFactory(thread=thread2, sender=contact2)
        MessageFactory(thread=thread2, sender=contact2)

        response = api_client.get(
            url_with_siret_query_param, **correctly_configured_header
        )
        result = response.json()["results"][0]

        # 1 message from domain1 + 2 from domain2 = 3 total
        assert result["metrics"]["storage_used"] == 3 * overhead

    @pytest.mark.django_db
    def test_blobs_with_identical_sizes_counted_separately(
        self, api_client, url, correctly_configured_header, settings
    ):
        """Two different blobs that happen to have the same compressed size
        must each be counted toward storage, not collapsed into one."""
        overhead = settings.METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE
        domain = MailDomainFactory(name="example.com")
        mailbox = MailboxFactory(domain=domain)
        MailboxAccessFactory(mailbox=mailbox)

        thread = ThreadFactory()
        ThreadAccessFactory(mailbox=mailbox, thread=thread)
        contact = ContactFactory(mailbox=mailbox)

        same_content = b"x" * 500
        msg1 = MessageFactory(thread=thread, sender=contact, raw_mime=same_content)
        msg2 = MessageFactory(thread=thread, sender=contact, raw_mime=same_content)

        assert msg1.blob.size_compressed == msg2.blob.size_compressed
        assert msg1.blob.pk != msg2.blob.pk

        response = api_client.get(url, **correctly_configured_header)
        result = response.json()["results"][0]

        expected = 2 * overhead + msg1.blob.size_compressed + msg2.blob.size_compressed
        assert result["metrics"]["storage_used"] == expected

    @pytest.mark.django_db
    def test_zero_storage_groups_not_skipped_with_custom_attribute(
        self,
        api_client,
        url_with_siret_query_param,
        correctly_configured_header,
    ):
        """When grouping by custom attribute, a group with users but zero
        storage must still include storage_used: 0 in its metrics."""
        siret = "12345678901234"

        domain_with_users = MailDomainFactory(
            name="users.com", custom_attributes={"siret": siret}
        )
        MailboxAccessFactory(mailbox__domain=domain_with_users)

        MailDomainFactory(name="empty.com", custom_attributes={"siret": siret})

        response = api_client.get(
            url_with_siret_query_param, **correctly_configured_header
        )
        results = response.json()["results"]

        assert len(results) == 1
        assert results[0]["siret"] == siret
        assert results[0]["metrics"]["storage_used"] == 0

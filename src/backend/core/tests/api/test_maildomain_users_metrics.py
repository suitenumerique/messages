"""Tests for the Maildomain users metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument,

from django.urls import reverse
from django.utils import timezone

import pytest

from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
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

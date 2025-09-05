"""Tests for the Prometheus metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument,

from importlib import import_module, reload
from random import randint
import sys
from django.test import override_settings
from django.urls import clear_url_caches, reverse
from django.utils import timezone

import pytest

from core.models import MailboxAccess
from core.factories import (
    MailboxAccessFactory,
    MailboxFactory,
    MailDomainFactory,
    UserFactory,
)


def check_results_for_key(
    results: dict | list,
    expected: dict[str, int],
    custom_attribute_key: str = None,
    custom_attribute_value: str = None,
):
    """
    Check that the results dictionary contains the expected keys and values.

    Args:
        results (dict|list): The results dictionary or list to check.
        expected (dict): A dictionary of expected keys and their corresponding expected values.
        key (str): The key to look for in the results dictionary, if empty checks at the top level.

    Raises:
        AssertionError: If any of the expected keys are missing or if their values do not match the expected values.
    """

    if custom_attribute_key is None:
        for expected_key, expected_value in expected.items():
            assert expected_key in results["metrics"], f"Missing key: {expected_key}"
            assert results["metrics"][expected_key] == expected_value
        return

    if not isinstance(results, list):
        raise ValueError(
            "When key is provided, results must be a list of dictionaries."
        )

    for result in results:
        if result[custom_attribute_key] == custom_attribute_value:
            for expected_key, expected_value in expected.items():
                assert expected_key in result["metrics"], (
                    f"Missing key: {expected_key} in result with key: {custom_attribute_key}"
                )
                assert result["metrics"][expected_key] == expected_value
            return
    raise KeyError(
        f"No result found with key: {custom_attribute_key} {custom_attribute_value}"
    )




@pytest.fixture
def url():
    """
    Fixture to return the URL for the Prometheus metrics endpoint.

    Returns:
        str: The URL for the Prometheus metrics endpoint.
    """
    return reverse("maildomain-users-metrics")

@pytest.fixture
def url_with_siret_query_param(url):
    """
    Fixture to return the URL for the Prometheus metrics endpoint with the SIRET query parameter.

    Args:
        url (str): The base URL for the Prometheus metrics endpoint.

    Returns:
        str: The URL for the Prometheus metrics endpoint with the SIRET query parameter.
    """
    return f"{url}?group_by_maildomain_custom_attribute=siret"


@pytest.fixture
def correctly_configured_header(settings):
    """
    Fixture to return the authentication header for the maildomain user metrics endpoint.

    Args:
        settings: The Django settings object.

    Returns:
        dict: A dictionary containing the authentication header.
    """
    return {"HTTP_AUTHORIZATION": f"Bearer {settings.MAILDOMAIN_USER_METRICS_API_KEY}"}

def create_domain_with_mailboxes(mailbox_counts: int = 1):
    """Create a maildomain with the given siret"""
    siret = randint(10000000000000, 99999999999999)
    domain = MailDomainFactory(custom_attributes={"siret": siret})
    mbs = MailboxFactory.create_batch(mailbox_counts, mail_domain=domain)
    return mbs, siret


def grant_access_to_mailbox_accessed_at(mailbox, user, accessed_at: timezone = None):
    """Grant access to the given mailboxes to the given users"""
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
def create_models_from_config(config)->list[MailboxAccess]:
    """Create maildomains, mailboxes and mailbox accesses from the given config"""
    accesses = []
    for domain_config in config:
        if "siret" in domain_config:
            domain = MailDomainFactory(custom_attributes={"siret": domain_config["siret"]})
        else:
            domain = MailDomainFactory()
        for mailbox_config in domain_config["mailboxes"]:
            mailbox = MailboxFactory(domain=domain)
            for user_config in mailbox_config["users"]:
                user = user_config["user"]
                accessed_at = user_config.get("accessed_at")
                accesses.append(grant_access_to_mailbox_accessed_at(mailbox, user, accessed_at))
    return accesses


class TestMailDomainUsersMetrics:
    """
    Test suite for the Prometheus metrics endpoint.

    This class contains tests to verify authentication, message status metrics,
    attachment count metrics, and attachment size metrics as reported by the
    Prometheus /metrics endpoint.
    """

    @pytest.fixture(autouse=True)
    def configure_settings(self):
        """Run before each test"""
        self.reload_urls()

    def reload_urls(self):
        """Reload the Django URL router"""
        clear_url_caches()
        if "messages.urls" in sys.modules:
            reload(sys.modules["messages.urls"])
        else:
            import_module("messages.urls")


    @pytest.mark.django_db
    def test_metrics_endpoint_requires_auth(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint requires authentication.

        Asserts that requests without or with invalid authentication are rejected (401),
        and requests with the correct API key are accepted (200).
        """
        settings.MAILDOMAIN_USER_METRICS_API_KEY = "test_api_key"

        # Test without authentication
        response = api_client.get(url)
        assert response.status_code == 401

        # Test with invalid authentication
        response = api_client.get(url, HTTP_AUTHORIZATION="Bearer invalid_token")
        assert response.status_code == 401

        # Test with authentication
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.MAILDOMAIN_USER_METRICS_API_KEY}"
        )
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_general_stats_without_query_params_with_no_user(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        """

        response = api_client.get(url, **correctly_configured_header)
        assert response.status_code == 200

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 0,
                "yau": 0,
                "mau": 0,
                "wau": 0,
            },
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_general_stats_without_query_params_with_users_no_access(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        # Create mailbox accesses for users
        MailboxAccessFactory.create_batch(3)
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
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_general_stats_without_query_params_with_users_access(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        # Create mailbox accesses for users
        mas = MailboxAccessFactory.create_batch(3)
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
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_general_stats_without_query_params_with_users_older_access(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        create_models_from_config([{
            "mailboxes": [
                {"users": [
                    {"user": UserFactory()} # Never accessed, only counted in tu
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=400)} # Old, only counted in tu
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=40)} # Only counted in tu + yau
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=10)} # Only counted in tu + yau + mau
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=1)} # Counted in tu + yau + mau + wau
                ]},
            ],
        }])

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
        )


    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_but_no_data(
        self, api_client, settings, url_with_siret_query_param, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        # Create mailbox accesses for users
        response = api_client.get(
            url_with_siret_query_param
            **correctly_configured_header,
        )
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert response.json()["results"] == []


    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_one_access(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        create_models_from_config([{
            "siret": "12345678901234",
            "mailboxes": [
                {"users": [
                    {"user": UserFactory()}
                ]},
            ],
        }])

        response = api_client.get(
            url_with_siret_query_param
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
            custom_attribute_key="siret",
            custom_attribute_value="12345678901234",
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_multiple_accesses_one_domain_one_user(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        user = UserFactory()

        mba = create_models_from_config([{
            "siret": "12345678901234",
            "mailboxes": [
                {"users": [
                    {"user": user}
                ]},
                {"users": [
                    {"user": user}
                ]},
            ],
        }])

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
            custom_attribute_key="siret",
            custom_attribute_value="12345678901234",
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
            custom_attribute_key="siret",
            custom_attribute_value="12345678901234",
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_multiple_accesses_multiple_domains_one_user(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret1 = "12345678901234"
        siret2 = "12345678909876"

        user = UserFactory()


        create_models_from_config([{
            "siret": siret1,
            "mailboxes": [
                {"users": [
                    {"user": user, "accessed_at": timezone.now() - timezone.timedelta(days=364)}
                ]}
            ],
        },
        {
            "siret": siret2,
            "mailboxes": [
                {"users": [
                    {"user": user, "accessed_at": timezone.now() - timezone.timedelta(days=29)}
                ]}
            ],
        }])

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
            custom_attribute_key="siret",
            custom_attribute_value=siret1,
        )

        check_results_for_key(
            response.json()["results"],
            {
                "tu": 1,  # Total unique users
                "yau": 1,  # Yearly active users
                "mau": 1,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            custom_attribute_key="siret",
            custom_attribute_value=siret2,
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_multiple_accesses_one_domain_one_mailbox_multiple_users(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config([{
            "siret": siret,
            "mailboxes": [
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=365)},
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=1)},
                ]},
            ],
        }])

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
            custom_attribute_key="siret",
            custom_attribute_value=siret,
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_multiple_accesses_one_domain_multiple_mailbox_multiple_users(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"


        create_models_from_config([{
            "siret": siret,
            "mailboxes": [
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=363)},
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=0)},
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=29)},
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=5)},
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(days=366)},
                ]},
            ],
        }])

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
            custom_attribute_key="siret",
            custom_attribute_value=siret,
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_just_before_day_cutoff(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config([{
            "siret": siret,
            "mailboxes": [
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=364, hours=23, minutes=59, seconds=59
                    )},
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=29, hours=23, minutes=59, seconds=59
                    )},
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=6, hours=23, minutes=59, seconds=59
                    )},
                ]},
            ],
        }])

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **correctly_configured_header,
        )

        assert response.status_code == 200
        print(response.json())
        assert response.json()["count"] == 1
        check_results_for_key(
            response.json()["results"],
            {
                "tu": 3,  # Total unique users
                "yau": 3,  # Yearly active users
                "mau": 2,  # Monthly active users
                "wau": 1,  # Weekly active users
            },
            custom_attribute_key="siret",
            custom_attribute_value=siret,
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_exact_days(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config([{
            "siret": siret,
            "mailboxes": [
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=365
                    )},
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=30
                    )},
                ]},
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=7
                    )},
                ]},
            ],
        }])

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
            custom_attribute_key="siret",
            custom_attribute_value=siret,
        )

    @pytest.mark.django_db
    def test_metrics_endpoint_should_not_count_uneven_custom_attributes(
        self, api_client, settings, url, correctly_configured_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """

        siret = "12345678901234"

        create_models_from_config([{
            "siret": siret,
            "mailboxes": [
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=150
                    )},
                ]},
            ],
        },
        {
            "mailboxes": [
                {"users": [
                    {"user": UserFactory(), "accessed_at": timezone.now() - timezone.timedelta(
                        days=15
                    )},
                ]},
            ],
        }])
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
                "mau": 0,  # Monthly active users
                "wau": 0,  # Weekly active users
            },
            custom_attribute_key="siret",
            custom_attribute_value=siret,
        )

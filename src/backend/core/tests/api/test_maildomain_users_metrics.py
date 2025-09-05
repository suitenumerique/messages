"""Tests for the Prometheus metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument,

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest

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
def maildomain_user_metrics_auth_header(settings):
    """
    Fixture to return the authentication header for the maildomain user metrics endpoint.

    Args:
        settings: The Django settings object.

    Returns:
        dict: A dictionary containing the authentication header.
    """
    settings.MAILDOMAIN_USER_METRICS_API_KEY = "test_api_key"
    return {"HTTP_AUTHORIZATION": f"Bearer {settings.MAILDOMAIN_USER_METRICS_API_KEY}"}


class TestMailDomainUsersMetrics:
    """
    Test suite for the Prometheus metrics endpoint.

    This class contains tests to verify authentication, message status metrics,
    attachment count metrics, and attachment size metrics as reported by the
    Prometheus /metrics endpoint.
    """

    @pytest.mark.django_db
    def test_metrics_endpoint_requires_auth(
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint requires authentication.

        Asserts that requests without or with invalid authentication are rejected (401),
        and requests with the correct API key are accepted (200).
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        """

        response = api_client.get(url, **maildomain_user_metrics_auth_header)
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        # Create mailbox accesses for users
        MailboxAccessFactory.create_batch(3)
        response = api_client.get(url, **maildomain_user_metrics_auth_header)
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        # Create mailbox accesses for users
        mas = MailboxAccessFactory.create_batch(3)
        for ma in mas:
            ma.accessed_at = timezone.now()
            ma.save()
        response = api_client.get(url, **maildomain_user_metrics_auth_header)
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        # Create mailbox accesses for users
        mas = MailboxAccessFactory.create_batch(5)

        # mas[0] never accessed, only counted in tu
        mas[1].accessed_at = timezone.now() - timezone.timedelta(
            days=400
        )  # Old, only counted in tu
        mas[2].accessed_at = timezone.now() - timezone.timedelta(
            days=40
        )  # Only counted in tu + yau
        mas[3].accessed_at = timezone.now() - timezone.timedelta(
            days=10
        )  # Only counted in tu + yau + mau
        mas[4].accessed_at = timezone.now() - timezone.timedelta(
            days=1
        )  # Counted in tu + yau + mau + wau
        for ma in mas:
            ma.save()
        response = api_client.get(url, **maildomain_user_metrics_auth_header)
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

    @override_settings(
        SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/maildomain",
            "type": "object",
            "title": "Maildomain custom fields",
            "additionalProperties": False,
            "properties": {
                "siret": {
                    "type": "string",
                    "title": "Siret",
                    "default": "",
                    "minLength": 14,
                    "maxLength": 14,
                    "pattern": "^[0-9]{14}$",
                },
            },
            "required": [],
        }
    )
    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_but_no_data(
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        # Create mailbox accesses for users
        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
        )
        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
        assert response.json()["count"] == 0
        assert response.json()["results"] == []

    @override_settings(
        SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/maildomain",
            "type": "object",
            "title": "Maildomain custom fields",
            "additionalProperties": False,
            "properties": {
                "siret": {
                    "type": "string",
                    "title": "Siret",
                    "default": "",
                    "minLength": 14,
                    "maxLength": 14,
                    "pattern": "^[0-9]{14}$",
                },
            },
            "required": [],
        }
    )
    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_one_access(
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        domain = MailDomainFactory(custom_attributes={"siret": "12345678901234"})
        mailbox = MailboxFactory(domain=domain)

        MailboxAccessFactory(mailbox=mailbox)
        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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

    @pytest.mark.django_db
    def test_metrics_endpoint_should_return_specific_stats_with_multiple_accesses_one_domain_one_user(
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        domain = MailDomainFactory(custom_attributes={"siret": "12345678901234"})
        user = UserFactory()
        mailbox1 = MailboxFactory(domain=domain)
        mailbox2 = MailboxFactory(domain=domain)

        ma1 = MailboxAccessFactory(mailbox=mailbox1, user=user)
        ma2 = MailboxAccessFactory(mailbox=mailbox2, user=user)

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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
        ma1.accessed_at = timezone.now() - timezone.timedelta(days=10)
        ma1.save()
        ma2.accessed_at = timezone.now() - timezone.timedelta(days=1)
        ma2.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        siret1 = "12345678901234"
        siret2 = "12345678909876"

        domain1 = MailDomainFactory(custom_attributes={"siret": siret1})
        domain2 = MailDomainFactory(custom_attributes={"siret": siret2})
        user = UserFactory()
        mailbox1 = MailboxFactory(domain=domain1)
        mailbox2 = MailboxFactory(domain=domain2)

        ma1 = MailboxAccessFactory(mailbox=mailbox1, user=user)
        ma2 = MailboxAccessFactory(mailbox=mailbox2, user=user)

        ma1.accessed_at = timezone.now() - timezone.timedelta(days=364)
        ma1.save()
        ma2.accessed_at = timezone.now() - timezone.timedelta(days=29)
        ma2.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        siret = "12345678901234"

        domain1 = MailDomainFactory(custom_attributes={"siret": siret})
        user1 = UserFactory()
        user2 = UserFactory()
        mailbox = MailboxFactory(domain=domain1)

        ma1 = MailboxAccessFactory(mailbox=mailbox, user=user1)
        ma2 = MailboxAccessFactory(mailbox=mailbox, user=user2)

        ma1.accessed_at = timezone.now() - timezone.timedelta(days=363)
        ma1.save()
        ma2.accessed_at = timezone.now() - timezone.timedelta(days=1)
        ma2.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        siret = "12345678901234"

        domain1 = MailDomainFactory(custom_attributes={"siret": siret})

        user1 = UserFactory()
        user2 = UserFactory()
        user3 = UserFactory()
        user4 = UserFactory()
        user5 = UserFactory()

        mailbox1 = MailboxFactory(domain=domain1)
        mailbox2 = MailboxFactory(domain=domain1)
        mailbox3 = MailboxFactory(domain=domain1)

        mas = [
            MailboxAccessFactory(mailbox=mailbox1, user=user1),
            MailboxAccessFactory(mailbox=mailbox1, user=user2),
            MailboxAccessFactory(mailbox=mailbox2, user=user3),
            MailboxAccessFactory(mailbox=mailbox2, user=user4),
            MailboxAccessFactory(mailbox=mailbox3, user=user5),
        ]

        mas[0].accessed_at = timezone.now() - timezone.timedelta(days=363)
        mas[1].accessed_at = timezone.now() - timezone.timedelta(days=0)
        mas[2].accessed_at = timezone.now() - timezone.timedelta(days=29)
        mas[3].accessed_at = timezone.now() - timezone.timedelta(days=5)
        mas[4].accessed_at = timezone.now() - timezone.timedelta(days=366)
        for ma in mas:
            ma.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        siret = "12345678901234"

        domain1 = MailDomainFactory(custom_attributes={"siret": siret})

        user1 = UserFactory()
        user2 = UserFactory()
        user3 = UserFactory()

        mailbox1 = MailboxFactory(domain=domain1)
        mailbox2 = MailboxFactory(domain=domain1)
        mailbox3 = MailboxFactory(domain=domain1)

        mas = [
            MailboxAccessFactory(mailbox=mailbox1, user=user1),
            MailboxAccessFactory(mailbox=mailbox2, user=user2),
            MailboxAccessFactory(mailbox=mailbox3, user=user3),
        ]

        mas[0].accessed_at = timezone.now() - timezone.timedelta(
            days=364, hours=23, minutes=59, seconds=59
        )
        mas[1].accessed_at = timezone.now() - timezone.timedelta(
            days=29, hours=23, minutes=59, seconds=59
        )
        mas[2].accessed_at = timezone.now() - timezone.timedelta(
            days=6, hours=23, minutes=59, seconds=59
        )
        for ma in mas:
            ma.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
        )

        assert response.status_code == 200
        assert "count" in response.json()
        assert "results" in response.json()
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        siret = "12345678901234"

        domain1 = MailDomainFactory(custom_attributes={"siret": siret})

        user1 = UserFactory()
        user2 = UserFactory()
        user3 = UserFactory()

        mailbox1 = MailboxFactory(domain=domain1)
        mailbox2 = MailboxFactory(domain=domain1)
        mailbox3 = MailboxFactory(domain=domain1)

        mas = [
            MailboxAccessFactory(mailbox=mailbox1, user=user1),
            MailboxAccessFactory(mailbox=mailbox2, user=user2),
            MailboxAccessFactory(mailbox=mailbox3, user=user3),
        ]

        mas[0].accessed_at = timezone.now() - timezone.timedelta(days=365)
        mas[1].accessed_at = timezone.now() - timezone.timedelta(days=30)
        mas[2].accessed_at = timezone.now() - timezone.timedelta(days=7)
        for ma in mas:
            ma.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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
        self, api_client, settings, url, maildomain_user_metrics_auth_header
    ):
        """
        Test that the metrics endpoint returns general stats when no query params are provided.

        Asserts that the response contains overall user and mailbox counts.
        Asserts that without accessing any mailbox, active user counts are zero.
        """
        settings.ENABLE_MAILDOMAIN_USERS_METRICS = True

        siret = "12345678901234"

        domain1 = MailDomainFactory(custom_attributes={"siret": siret})
        domain2 = MailDomainFactory(custom_attributes={})

        user1 = UserFactory()
        user2 = UserFactory()

        mailbox1 = MailboxFactory(domain=domain1)
        mailbox2 = MailboxFactory(domain=domain2)

        mas = [
            MailboxAccessFactory(mailbox=mailbox1, user=user1),
            MailboxAccessFactory(mailbox=mailbox2, user=user2),
        ]

        mas[0].accessed_at = timezone.now() - timezone.timedelta(days=150)
        mas[1].accessed_at = timezone.now() - timezone.timedelta(days=15)

        for ma in mas:
            ma.save()

        response = api_client.get(
            f"{url}?group_by_maildomain_custom_attribute=siret",
            **maildomain_user_metrics_auth_header,
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

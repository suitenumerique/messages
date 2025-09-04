"""Tests for the Prometheus metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument,

from django.urls import reverse

import pytest


@pytest.fixture
def url():
    """
    Fixture to return the URL for the Prometheus metrics endpoint.

    Returns:
        str: The URL for the Prometheus metrics endpoint.
    """
    return reverse("maildomain-users-metrics")


class TestMailDomainUsersMetrics:
    """
    Test suite for the Prometheus metrics endpoint.

    This class contains tests to verify authentication, message status metrics,
    attachment count metrics, and attachment size metrics as reported by the
    Prometheus /metrics endpoint.
    """

    @pytest.mark.django_db
    def test_metrics_endpoint_requires_auth(self, api_client, settings, url):
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

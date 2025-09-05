"""Tests for the Prometheus metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument

import sys
from importlib import import_module, reload

from django.test import override_settings
from django.urls import clear_url_caches, reverse

import pytest
from prometheus_client.parser import text_string_to_metric_families

from core.enums import MessageDeliveryStatusChoices
from core.factories import AttachmentFactory, MessageRecipientFactory


@pytest.fixture
def url():
    """
    Fixture to return the URL for the Prometheus metrics endpoint.

    Returns:
        str: The URL for the Prometheus metrics endpoint.
    """
    return reverse("prometheus-django-metrics")


def response_to_metrics_dict(response, with_label=None):
    """Convert a response to a dictionary of metrics"""
    d = {}
    for family in text_string_to_metric_families(response.content.decode("utf-8")):
        for sample in family.samples:
            if with_label:
                d[(sample.name, sample.labels.get(with_label))] = sample.value
            else:
                d[sample.name] = sample.value
    return d


class TestPrometheusMetrics:
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
    def test_metrics_endpoint_requires_auth(self, api_client, settings, url):
        """
        Test that the metrics endpoint requires authentication.

        Asserts that requests without or with invalid authentication are rejected (401),
        and requests with the correct API key are accepted (200).
        """

        # Test without authentication
        response = api_client.get(url)
        assert response.status_code == 401

        # Test with invalid authentication
        response = api_client.get(url, HTTP_AUTHORIZATION="Bearer invalid_token")
        assert response.status_code == 401

        # Test with authentication
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )
        assert response.status_code == 200

    @override_settings(ENABLE_PROMETHEUS=False)
    @pytest.mark.django_db
    def test_metrics_endpoint_prometheus_disabled(self, api_client, settings, url):
        """
        Test that the metrics endpoint is disabled when ENABLE_PROMETHEUS is False.

        Asserts that requests are rejected (404).
        """

        self.reload_urls()

        # Test with authentication
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )
        assert response.status_code == 404

    @pytest.mark.django_db
    def test_get_messages_with_status_count_zero(self, api_client, settings, url):
        """
        Test that message status metrics are zero when there are no messages.

        Asserts that all message status counts are reported as zero.
        """
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response, with_label="status")

        assert (
            metrics[("message_status_count", MessageDeliveryStatusChoices.SENT.label)]
            == 0
        )
        assert (
            metrics[
                ("message_status_count", MessageDeliveryStatusChoices.INTERNAL.label)
            ]
            == 0
        )
        assert (
            metrics[("message_status_count", MessageDeliveryStatusChoices.FAILED.label)]
            == 0
        )
        assert (
            metrics[("message_status_count", MessageDeliveryStatusChoices.RETRY.label)]
            == 0
        )

    @pytest.mark.django_db
    def test_get_messages_with_status_count(self, api_client, settings, url):
        """
        Test that message status metrics reflect the correct count for each status.

        Asserts that the metrics endpoint reports the correct count for each
        MessageDeliveryStatusChoices value.
        """

        statuses_to_count = {
            MessageDeliveryStatusChoices.SENT: 1,
            MessageDeliveryStatusChoices.INTERNAL: 2,
            MessageDeliveryStatusChoices.FAILED: 3,
            MessageDeliveryStatusChoices.RETRY: 4,
        }
        for status, count in statuses_to_count.items():
            MessageRecipientFactory.create_batch(size=count, delivery_status=status)

        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response, with_label="status")

        for status, count in statuses_to_count.items():
            assert metrics[("message_status_count", status.label)] == count

    @pytest.mark.django_db
    def test_get_attachments_count_zero(self, api_client, settings, url):
        """
        Test that the attachment count metric is zero when there are no attachments.

        Asserts that the 'draft_attachment_count' metric is reported as zero.
        """
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response)

        assert metrics[("draft_attachment_count")] == 0

    @pytest.mark.parametrize("attachment_count", [0, 1, 10])
    @pytest.mark.django_db
    def test_get_attachments_count(self, api_client, settings, url, attachment_count):
        """
        Test that the attachment count metric matches the number of created attachments.

        Args:
            attachment_count (int): The number of attachments to create.

        Asserts that the 'draft_attachment_count' metric equals the number of created attachments.
        """

        AttachmentFactory.create_batch(size=attachment_count)

        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response)

        assert metrics[("draft_attachment_count")] == attachment_count

    @pytest.mark.django_db
    def test_get_attachments_size_no_attachment(self, api_client, settings, url):
        """
        Test that the total attachment size metric is zero when there are no attachments.

        Asserts that the 'draft_attachments_total_size_bytes' metric is reported as zero.
        """

        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response)

        assert metrics[("draft_attachments_total_size_bytes")] == 0

    @pytest.mark.parametrize("blob_size", [0, 150, 1000])
    @pytest.mark.django_db
    def test_get_attachments_size_one_attachment(
        self, api_client, settings, url, blob_size
    ):
        """
        Test that the total attachment size metric matches the size of a single attachment.

        Args:
            blob_size (int): The size of the blob to create.

        Asserts that the 'draft_attachments_total_size_bytes' metric equals the blob size.
        """
        AttachmentFactory(blob_size=blob_size)

        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response)

        assert metrics[("draft_attachments_total_size_bytes")] == blob_size

    @pytest.mark.parametrize("blob_sizes", [[0, 0], [0, 150, 1000], [1, 2, 3, 4, 5]])
    @pytest.mark.django_db
    def test_get_attachments_size_multiple_attachments(
        self, api_client, settings, url, blob_sizes
    ):
        """
        Test that the total attachment size metric matches the sum of multiple attachments.

        Args:
            blobs_size (list): List of blob sizes to create.

        Asserts that the 'draft_attachments_total_size_bytes' metric equals the sum of blob sizes.
        """
        for blob_size in blob_sizes:
            AttachmentFactory(blob_size=blob_size)

        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        metrics = response_to_metrics_dict(response)

        assert metrics[("draft_attachments_total_size_bytes")] == sum(blob_sizes)

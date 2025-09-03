"""Tests for the Prometheus metrics endpoint."""
# pylint: disable=redefined-outer-name, unused-argument,

from django.urls import reverse

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


@pytest.fixture
def messages_for_count():
    """
    Fixture to create test messages with each delivery statuses.

    Creates a number of MessageRecipient instances for each delivery status
    defined in MessageDeliveryStatusChoices.
    """
    statuses_to_count = {
        MessageDeliveryStatusChoices.SENT: 1,
        MessageDeliveryStatusChoices.INTERNAL: 2,
        MessageDeliveryStatusChoices.FAILED: 3,
        MessageDeliveryStatusChoices.RETRY: 4,
    }
    for status, count in statuses_to_count.items():
        for _ in range(count):
            MessageRecipientFactory(delivery_status=status)


class TestPrometheusMetrics:
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
        settings.ENABLE_PROMETHEUS = True
        settings.PROMETHEUS_API_KEY = "test_api_key"

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

    @pytest.mark.django_db
    def test_get_messages_with_status_count_zero(self, api_client, settings, url):
        """
        Test that message status metrics are zero when there are no messages.

        Asserts that all message status counts are reported as zero.
        """
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )
        status_visited = {
            MessageDeliveryStatusChoices.SENT.label: False,
            MessageDeliveryStatusChoices.INTERNAL.label: False,
            MessageDeliveryStatusChoices.FAILED.label: False,
            MessageDeliveryStatusChoices.RETRY.label: False,
        }
        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "message_status_count":
                    status_visited[sample.labels["status"]] = True
                    assert sample.value == 0

        for visited in status_visited.values():
            assert visited is True

    @pytest.mark.django_db
    def test_get_messages_with_status_count(
        self, api_client, settings, url, messages_for_count
    ):
        """
        Test that message status metrics reflect the correct count for each status.

        Asserts that the metrics endpoint reports the correct count for each
        MessageDeliveryStatusChoices value.
        """
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        status_visited = {
            MessageDeliveryStatusChoices.SENT.label: False,
            MessageDeliveryStatusChoices.INTERNAL.label: False,
            MessageDeliveryStatusChoices.FAILED.label: False,
            MessageDeliveryStatusChoices.RETRY.label: False,
        }

        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "message_status_count":
                    status_visited[sample.labels["status"]] = True
                    if (
                        sample.labels["status"]
                        == MessageDeliveryStatusChoices.SENT.label
                    ):
                        assert sample.value == 1
                    elif (
                        sample.labels["status"]
                        == MessageDeliveryStatusChoices.INTERNAL.label
                    ):
                        assert sample.value == 2
                    elif (
                        sample.labels["status"]
                        == MessageDeliveryStatusChoices.FAILED.label
                    ):
                        assert sample.value == 3
                    elif (
                        sample.labels["status"]
                        == MessageDeliveryStatusChoices.RETRY.label
                    ):
                        assert sample.value == 4
                    else:
                        raise AssertionError(
                            f"Unexpected status label: {sample.labels['status']}"
                        )
        for visited in status_visited.values():
            assert visited is True

    @pytest.mark.django_db
    def test_get_attachments_count_zero(self, api_client, settings, url):
        """
        Test that the attachment count metric is zero when there are no attachments.

        Asserts that the 'attachment_count' metric is reported as zero.
        """
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "attachment_count":
                    assert sample.value == 0

    @pytest.mark.parametrize("attachment_count", [0, 1, 10])
    @pytest.mark.django_db
    def test_get_attachments_count(self, api_client, settings, url, attachment_count):
        """
        Test that the attachment count metric matches the number of created attachments.

        Args:
            attachment_count (int): The number of attachments to create.

        Asserts that the 'attachment_count' metric equals the number of created attachments.
        """
        _ = [AttachmentFactory() for _ in range(attachment_count)]

        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "attachment_count":
                    assert sample.value == attachment_count

    @pytest.mark.django_db
    def test_get_attachments_size_no_attachment(self, api_client, settings, url):
        """
        Test that the total attachment size metric is zero when there are no attachments.

        Asserts that the 'attachments_total_size_bytes' metric is reported as zero.
        """
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "attachments_total_size_bytes":
                    assert sample.value == 0

    @pytest.mark.parametrize("blob_size", [0, 150, 1000])
    @pytest.mark.django_db
    def test_get_attachments_size_one_attachment(
        self, api_client, settings, url, blob_size
    ):
        """
        Test that the total attachment size metric matches the size of a single attachment.

        Args:
            blob_size (int): The size of the blob to create.

        Asserts that the 'attachments_total_size_bytes' metric equals the blob size.
        """
        AttachmentFactory(blob_size=blob_size)
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "attachments_total_size_bytes":
                    assert sample.value == blob_size

    @pytest.mark.parametrize("blobs_size", [[0, 0], [0, 150, 1000], [1, 2, 3, 4, 5]])
    @pytest.mark.django_db
    def test_get_attachments_size_multiple_attachments(
        self, api_client, settings, url, blobs_size
    ):
        """
        Test that the total attachment size metric matches the sum of multiple attachments.

        Args:
            blobs_size (list): List of blob sizes to create.

        Asserts that the 'attachments_total_size_bytes' metric equals the sum of blob sizes.
        """
        _ = [AttachmentFactory(blob_size=blob_size) for blob_size in blobs_size]
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}"
        )

        for family in text_string_to_metric_families(response.content.decode("utf-8")):
            for sample in family.samples:
                if sample.name == "attachments_total_size_bytes":
                    assert sample.value == sum(blobs_size)

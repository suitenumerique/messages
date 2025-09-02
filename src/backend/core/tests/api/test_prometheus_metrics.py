import pytest
from django.urls import reverse

from prometheus_client.parser import text_string_to_metric_families

from core.factories import AttachmentFactory, MessageRecipientFactory
from core.enums import MessageDeliveryStatusChoices

@pytest.fixture
def url():
    return reverse('prometheus-django-metrics')

@pytest.fixture
def messages_for_count():
    """Create test messages with various delivery statuses."""
    statuses_to_count = {
        MessageDeliveryStatusChoices.SENT: 1,
        MessageDeliveryStatusChoices.INTERNAL: 2,
        MessageDeliveryStatusChoices.FAILED: 3,
        MessageDeliveryStatusChoices.RETRY: 4,
    }
    for status, count in statuses_to_count.items():
        for _ in range(count):
            MessageRecipientFactory(
                delivery_status=status
            )

class TestPrometheusMetrics:
    @pytest.mark.django_db
    def test_metrics_endpoint_requires_auth(self, api_client, settings, url):
        settings.ENABLE_PROMETHEUS = True
        settings.PROMETHEUS_API_KEY = "test_api_key"

        # Test without authentication
        response = api_client.get(url)
        assert response.status_code == 401

        # Test with invalid authentication
        response = api_client.get(url, HTTP_AUTHORIZATION="Bearer invalid_token")
        assert response.status_code == 401

        # Test with authentication
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_get_messages_with_status_count_zero(self, api_client, settings, url):
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")
        status_visited = {
            MessageDeliveryStatusChoices.SENT.label: False,
            MessageDeliveryStatusChoices.INTERNAL.label: False,
            MessageDeliveryStatusChoices.FAILED.label: False,
            MessageDeliveryStatusChoices.RETRY.label: False,
        }
        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "message_status_count":
                    status_visited[sample.labels["status"]] = True
                    assert sample.value == 0

        for visited in status_visited.values():
            assert visited is True

    @pytest.mark.django_db
    def test_get_messages_with_status_count(self, api_client, settings, url, messages_for_count):
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")

        status_visited = {
            MessageDeliveryStatusChoices.SENT.label: False,
            MessageDeliveryStatusChoices.INTERNAL.label: False,
            MessageDeliveryStatusChoices.FAILED.label: False,
            MessageDeliveryStatusChoices.RETRY.label: False,
        }

        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "message_status_count":
                    status_visited[sample.labels["status"]] = True
                    if sample.labels["status"] == MessageDeliveryStatusChoices.SENT.label:
                        assert sample.value == 1
                    elif sample.labels["status"] == MessageDeliveryStatusChoices.INTERNAL.label:
                        assert sample.value == 2
                    elif sample.labels["status"] == MessageDeliveryStatusChoices.FAILED.label:
                        assert sample.value == 3
                    elif sample.labels["status"] == MessageDeliveryStatusChoices.RETRY.label:
                        assert sample.value == 4
                    else:
                        raise AssertionError(f"Unexpected status label: {sample.labels['status']}")
        for visited in status_visited.values():
            assert visited is True

    @pytest.mark.django_db
    def test_get_attachments_count_zero(self, api_client, settings, url):
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")

        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "attachment_count":
                    assert sample.value == 0


    @pytest.mark.parametrize("attachment_count", [0, 1, 10])
    @pytest.mark.django_db
    def test_get_attachments_count(self, api_client, settings, url, attachment_count):
        [AttachmentFactory() for _ in range(attachment_count)]

        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")

        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "attachment_count":
                    assert sample.value == attachment_count

    @pytest.mark.django_db
    def test_get_attachments_size_no_attachment(self, api_client, settings, url):
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")

        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "attachments_total_size_bytes":
                    assert sample.value == 0

    @pytest.mark.parametrize("blob_size", [0, 150, 1000])
    @pytest.mark.django_db
    def test_get_attachments_size_one_attachment(self, api_client, settings, url, blob_size):
        AttachmentFactory(blob_size=blob_size)
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")

        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "attachments_total_size_bytes":
                    assert sample.value == blob_size

    @pytest.mark.parametrize("blobs_size", [[0,0], [0, 150, 1000], [1, 2, 3, 4, 5]])
    @pytest.mark.django_db
    def test_get_attachments_size_multiple_attachments(self, api_client, settings, url, blobs_size):
        [AttachmentFactory(blob_size=blob_size) for blob_size in blobs_size]
        settings.PROMETHEUS_API_KEY = "test_api_key"
        response = api_client.get(url, HTTP_AUTHORIZATION=f"Bearer {settings.PROMETHEUS_API_KEY}")

        for family in text_string_to_metric_families(response.content.decode('utf-8')):
            for sample in family.samples:
                if sample.name == "attachments_total_size_bytes":
                    assert sample.value == sum(blobs_size)


"""Tests for the generic email submission endpoint (POST /submit/)."""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,import-outside-toplevel

import uuid
from unittest.mock import patch

import pytest

from core.factories import MailboxFactory, MailDomainFactory

SUBMIT_URL = "/api/v1.0/submit/"

MINIMAL_MIME = (
    b"From: contact@company.com\r\n"
    b"To: attendee@example.com\r\n"
    b"Subject: Team Meeting\r\n"
    b"Message-ID: <test-123@company.com>\r\n"
    b"Date: Mon, 30 Mar 2026 10:00:00 +0000\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Hello world\r\n"
)

CREATE_MSG_MOCK = "core.api.viewsets.submit._create_message_from_inbound"
PREPARE_MOCK = "core.api.viewsets.submit.prepare_outbound_message"
TASK_MOCK = "core.api.viewsets.submit.send_message_task"


@pytest.fixture
def auth_header(settings):
    """Returns the authentication header for the submit endpoint."""
    settings.CALENDARS_API_KEY = "test-calendar-key"
    return {"HTTP_X_SERVICE_AUTH": "Bearer test-calendar-key"}


@pytest.fixture
def domain():
    return MailDomainFactory(name="company.com")


@pytest.fixture
def mailbox(domain):
    return MailboxFactory(local_part="contact", domain=domain)


# =============================================================================
# Authentication
# =============================================================================


@pytest.mark.django_db
class TestSubmitAuth:
    """Authentication tests for the submit endpoint."""

    def test_no_auth_returns_403(self, client, mailbox):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 403

    def test_wrong_token_returns_403(self, client, settings, mailbox):
        settings.CALENDARS_API_KEY = "correct-key"
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_SERVICE_AUTH="Bearer wrong-key",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 403

    def test_no_key_configured_returns_403(self, client, settings, mailbox):
        settings.CALENDARS_API_KEY = None
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_SERVICE_AUTH="Bearer some-key",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 403

    def test_get_method_not_allowed(self, client, auth_header, mailbox):
        response = client.get(
            SUBMIT_URL,
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 405


# =============================================================================
# Validation
# =============================================================================


@pytest.mark.django_db
class TestSubmitValidation:
    """Input validation tests."""

    def test_missing_both_headers_returns_400(self, client, auth_header):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            **auth_header,
        )
        assert response.status_code == 400
        assert "X-Mail-From" in response.json()["detail"]

    def test_missing_x_rcpt_to_returns_400(self, client, auth_header, mailbox):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            **auth_header,
        )
        assert response.status_code == 400

    def test_missing_x_mail_from_returns_400(self, client, auth_header):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 400

    def test_unknown_mailbox_returns_404(self, client, auth_header):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(uuid.uuid4()),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 404

    def test_invalid_uuid_in_x_mail_from_returns_error(self, client, auth_header):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM="not-a-uuid",
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code in (400, 404)

    def test_empty_body_returns_400(self, client, auth_header, mailbox):
        response = client.post(
            SUBMIT_URL,
            data=b"",
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 400

    def test_sender_mismatch_returns_403(self, client, auth_header, domain):
        other_mailbox = MailboxFactory(local_part="other", domain=domain)
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,  # From: contact@company.com
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(other_mailbox.id),  # other@company.com
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 403

    def test_empty_rcpt_to_returns_400(self, client, auth_header, mailbox):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="  ,  ",
            **auth_header,
        )
        assert response.status_code == 400


# =============================================================================
# Message creation + DKIM signing + async dispatch
# =============================================================================


@pytest.mark.django_db
class TestSubmitDispatch:
    """Verify message creation, synchronous signing, and async dispatch."""

    @patch(TASK_MOCK)
    @patch(PREPARE_MOCK, return_value=True)
    @patch(CREATE_MSG_MOCK)
    def test_accepted(
        self, mock_create, mock_prepare, mock_task, client, auth_header, mailbox
    ):
        fake_message = type("Message", (), {"id": uuid.uuid4()})()
        mock_create.return_value = fake_message

        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["message_id"] == str(fake_message.id)

        # Message created with is_outbound=True
        mock_create.assert_called_once()
        assert mock_create.call_args[1]["is_outbound"] is True
        assert mock_create.call_args[1]["mailbox"] == mailbox

        # prepare_outbound_message called with raw_mime
        mock_prepare.assert_called_once()
        assert mock_prepare.call_args[1]["raw_mime"] == MINIMAL_MIME

        # Async task dispatched
        mock_task.delay.assert_called_once_with(str(fake_message.id))

    @patch(TASK_MOCK)
    @patch(PREPARE_MOCK, return_value=True)
    @patch(CREATE_MSG_MOCK)
    def test_create_message_failure_returns_500(
        self, mock_create, mock_prepare, mock_task, client, auth_header, mailbox
    ):
        mock_create.return_value = None

        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )

        assert response.status_code == 500
        assert "create message" in response.json()["detail"].lower()
        mock_prepare.assert_not_called()
        mock_task.delay.assert_not_called()

    @patch(TASK_MOCK)
    @patch(PREPARE_MOCK, return_value=False)
    @patch(CREATE_MSG_MOCK)
    def test_prepare_failure_returns_500(
        self, mock_create, mock_prepare, mock_task, client, auth_header, mailbox
    ):
        mock_create.return_value = type("Message", (), {"id": uuid.uuid4()})()

        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )

        assert response.status_code == 500
        assert "prepare" in response.json()["detail"].lower()
        mock_task.delay.assert_not_called()


# =============================================================================
# Integration — real pipeline, only mock the async SMTP delivery
# =============================================================================


@pytest.mark.django_db
class TestSubmitIntegration:
    """End-to-end tests that run the full pipeline (message creation, DKIM
    signing, blob storage) and only mock the final async SMTP task."""

    @patch(TASK_MOCK)
    def test_full_pipeline(self, mock_task, client, auth_header, mailbox):
        """Submit creates a Message with thread, recipients, blob, and dispatches delivery."""
        mailbox_email = str(mailbox)
        # X-Rcpt-To matches the To: header in MINIMAL_MIME (attendee@example.com)
        rcpt_to = "attendee@example.com"

        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO=rcpt_to,
            **auth_header,
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        message_id = data["message_id"]

        # Verify message in DB
        from core.models import Message, ThreadAccess

        message = Message.objects.get(id=message_id)
        assert message.subject == "Team Meeting"
        assert message.is_sender is True
        assert message.is_draft is False  # finalized by prepare_outbound_message
        assert message.sender.email == mailbox_email
        assert message.blob is not None  # DKIM-signed MIME stored

        # Thread was created and mailbox has access
        assert message.thread is not None
        assert ThreadAccess.objects.filter(
            thread=message.thread, mailbox=mailbox
        ).exists()

        # Recipient was created (from the parsed To: header)
        assert message.recipients.filter(contact__email=rcpt_to).exists()

        # Async delivery dispatched
        mock_task.delay.assert_called_once_with(str(message.id))

    @patch(TASK_MOCK)
    def test_multiple_recipients_creates_all(
        self, mock_task, client, auth_header, mailbox
    ):
        """Multiple X-Rcpt-To addresses each get a recipient record."""
        mailbox_email = str(mailbox)
        mime = (
            f"From: {mailbox_email}\r\n"
            f"To: a@example.com, b@example.com\r\n"
            f"Subject: Multi\r\n"
            f"Message-ID: <multi@example.com>\r\n"
            f"Date: Mon, 30 Mar 2026 10:00:00 +0000\r\n"
            f"MIME-Version: 1.0\r\n"
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"body\r\n"
        ).encode()

        response = client.post(
            SUBMIT_URL,
            data=mime,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="a@example.com, b@example.com",
            **auth_header,
        )

        assert response.status_code == 202
        from core.models import Message

        message = Message.objects.get(id=response.json()["message_id"])
        recipient_emails = set(
            message.recipients.values_list("contact__email", flat=True)
        )
        assert "a@example.com" in recipient_emails
        assert "b@example.com" in recipient_emails

    @patch(TASK_MOCK)
    def test_bcc_recipients_created(self, mock_task, client, auth_header, mailbox):
        """Bcc recipients from MIME headers are created as MessageRecipient rows."""
        mailbox_email = str(mailbox)
        mime = (
            f"From: {mailbox_email}\r\n"
            f"To: visible@example.com\r\n"
            f"Bcc: hidden@example.com\r\n"
            f"Subject: With Bcc\r\n"
            f"Message-ID: <bcc@example.com>\r\n"
            f"Date: Mon, 30 Mar 2026 10:00:00 +0000\r\n"
            f"MIME-Version: 1.0\r\n"
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"body\r\n"
        ).encode()

        response = client.post(
            SUBMIT_URL,
            data=mime,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="visible@example.com, hidden@example.com",
            **auth_header,
        )

        assert response.status_code == 202
        from core.models import Message

        message = Message.objects.get(id=response.json()["message_id"])
        recipient_emails = set(
            message.recipients.values_list("contact__email", flat=True)
        )
        assert "visible@example.com" in recipient_emails
        assert "hidden@example.com" in recipient_emails

    @patch(TASK_MOCK)
    def test_cc_recipients_created(self, mock_task, client, auth_header, mailbox):
        """Cc recipients from MIME headers are created as MessageRecipient rows."""
        mailbox_email = str(mailbox)
        mime = (
            f"From: {mailbox_email}\r\n"
            f"To: to@example.com\r\n"
            f"Cc: cc@example.com\r\n"
            f"Subject: With Cc\r\n"
            f"Message-ID: <cc@example.com>\r\n"
            f"Date: Mon, 30 Mar 2026 10:00:00 +0000\r\n"
            f"MIME-Version: 1.0\r\n"
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"body\r\n"
        ).encode()

        response = client.post(
            SUBMIT_URL,
            data=mime,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="to@example.com, cc@example.com",
            **auth_header,
        )

        assert response.status_code == 202
        from core.enums import MessageRecipientTypeChoices
        from core.models import Message

        message = Message.objects.get(id=response.json()["message_id"])
        assert message.recipients.filter(
            contact__email="to@example.com",
            type=MessageRecipientTypeChoices.TO,
        ).exists()
        assert message.recipients.filter(
            contact__email="cc@example.com",
            type=MessageRecipientTypeChoices.CC,
        ).exists()

"""Tests for the generic email submission endpoint (POST /submit/)."""
# pylint: disable=redefined-outer-name,missing-function-docstring,unused-argument,import-outside-toplevel

import uuid
from unittest.mock import MagicMock, patch

import pytest
from dkim import verify as dkim_verify

from core.enums import ChannelApiKeyScope, ChannelScopeLevel
from core.factories import MailboxFactory, MailDomainFactory, make_api_key_channel
from core.mda.signing import generate_dkim_key

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


def _make_api_key_channel(**kwargs):
    """Thin wrapper around the shared factory pre-loaded with the
    submit-endpoint default scope (messages:send)."""
    kwargs.setdefault("scopes", (ChannelApiKeyScope.MESSAGES_SEND.value,))
    kwargs.setdefault("name", "test-key")
    return make_api_key_channel(**kwargs)


@pytest.fixture
def auth_header():
    """Build a global-scope api_key with messages:send and return the auth headers."""
    channel, plaintext = _make_api_key_channel()
    return {
        "HTTP_X_CHANNEL_ID": str(channel.id),
        "HTTP_X_API_KEY": plaintext,
    }


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

    def test_no_auth_returns_401(self, client, mailbox):
        """No auth headers → 401 via DRF NotAuthenticated."""
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 401

    def test_wrong_token_returns_401(self, client, mailbox):
        """Invalid credentials are an authentication failure → 401."""
        channel, _plaintext = _make_api_key_channel()
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY="not-the-real-key",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 401

    def test_unknown_channel_returns_401(self, client, mailbox):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(uuid.uuid4()),
            HTTP_X_API_KEY="anything",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 401

    def test_missing_scope_returns_403(self, client, mailbox):
        channel, plaintext = _make_api_key_channel(
            scopes=(ChannelApiKeyScope.MAILBOXES_READ.value,),
        )
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 403

    def test_mailbox_scope_wrong_mailbox_returns_403(self, client, domain, mailbox):
        other_mailbox = MailboxFactory(local_part="other", domain=domain)
        channel, plaintext = _make_api_key_channel(
            scope_level=ChannelScopeLevel.MAILBOX,
            mailbox=other_mailbox,
        )
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,  # from contact@company.com
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
            HTTP_X_MAIL_FROM=str(mailbox.id),  # contact@company.com
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 403

    def test_maildomain_scope_wrong_domain_returns_403(self, client, mailbox):
        other_domain = MailDomainFactory(name="other.test")
        channel, plaintext = _make_api_key_channel(
            scope_level=ChannelScopeLevel.MAILDOMAIN,
            maildomain=other_domain,
        )
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
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

    @pytest.mark.parametrize(
        "role_name",
        ["VIEWER", "EDITOR"],
    )
    def test_user_scope_non_sending_role_cannot_submit(
        self, client, mailbox, role_name
    ):
        """Regression: a user-scope api_key whose owner does not have a
        SENDER-or-better role on the mailbox MUST NOT be able to submit.
        Both VIEWER and EDITOR are below the threshold —
        ``MAILBOX_ROLES_CAN_SEND = [SENDER, ADMIN]`` — so neither can
        send through a personal api_key. The fix is in
        ``Channel.api_key_covers``'s ``mailbox_roles=`` path."""
        from core.enums import MailboxRoleChoices
        from core.factories import MailboxAccessFactory, UserFactory

        owner = UserFactory(email=f"{role_name.lower()}@oidc.example.com")
        MailboxAccessFactory(
            mailbox=mailbox,
            user=owner,
            role=getattr(MailboxRoleChoices, role_name),
        )

        channel, plaintext = _make_api_key_channel(
            scope_level=ChannelScopeLevel.USER,
            user=owner,
            name=f"{role_name.lower()}-personal",
        )

        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_CHANNEL_ID=str(channel.id),
            HTTP_X_API_KEY=plaintext,
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
        )
        assert response.status_code == 403, response.content

    @pytest.mark.parametrize(
        "role_name",
        ["SENDER", "ADMIN"],
    )
    def test_user_scope_sending_role_can_submit(self, client, mailbox, role_name):
        """Companion to the negative test: a user-scope api_key whose
        owner has SENDER or ADMIN access *is* allowed through. Both roles
        are in ``MAILBOX_ROLES_CAN_SEND``. The pipeline is mocked so this
        test only exercises the auth+permission+covers layer."""
        from core.enums import MailboxRoleChoices
        from core.factories import MailboxAccessFactory, UserFactory

        owner = UserFactory(email=f"{role_name.lower()}@oidc.example.com")
        MailboxAccessFactory(
            mailbox=mailbox,
            user=owner,
            role=getattr(MailboxRoleChoices, role_name),
        )

        channel, plaintext = _make_api_key_channel(
            scope_level=ChannelScopeLevel.USER,
            user=owner,
            name=f"{role_name.lower()}-personal",
        )

        fake_message = MagicMock()
        fake_message.id = uuid.uuid4()
        fake_message.recipients.values_list.return_value = []

        with (
            patch(CREATE_MSG_MOCK, return_value=fake_message),
            patch(PREPARE_MOCK, return_value=True),
            patch(TASK_MOCK),
        ):
            response = client.post(
                SUBMIT_URL,
                data=MINIMAL_MIME,
                content_type="message/rfc822",
                HTTP_X_CHANNEL_ID=str(channel.id),
                HTTP_X_API_KEY=plaintext,
                HTTP_X_MAIL_FROM=str(mailbox.id),
                HTTP_X_RCPT_TO="attendee@example.com",
            )
        assert response.status_code == 202, response.content


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

    def test_invalid_uuid_in_x_mail_from_returns_404(self, client, auth_header):
        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM="not-a-uuid",
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 404

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

    def _fake_message(self):
        """Create a fake Message with a recipients manager stub."""
        msg = MagicMock()
        msg.id = uuid.uuid4()
        msg.recipients.values_list.return_value = ["attendee@example.com"]
        return msg

    @patch(TASK_MOCK)
    @patch(PREPARE_MOCK, return_value=True)
    @patch(CREATE_MSG_MOCK)
    def test_accepted(
        self, mock_create, mock_prepare, mock_task, client, auth_header, mailbox
    ):
        fake_message = self._fake_message()
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
        mock_create.return_value = self._fake_message()

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
    def test_bcc_via_envelope(self, mock_task, client, auth_header, mailbox):
        """BCC works via X-Rcpt-To: the recipient is in the envelope but NOT
        in the MIME headers — just like real SMTP BCC."""
        mailbox_email = str(mailbox)
        # MIME only has To: visible@example.com — no Bcc header
        mime = (
            f"From: {mailbox_email}\r\n"
            f"To: visible@example.com\r\n"
            f"Subject: With Bcc\r\n"
            f"Message-ID: <bcc@example.com>\r\n"
            f"Date: Mon, 30 Mar 2026 10:00:00 +0000\r\n"
            f"MIME-Version: 1.0\r\n"
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"body\r\n"
        ).encode()

        # X-Rcpt-To includes both visible and hidden (BCC) recipients
        response = client.post(
            SUBMIT_URL,
            data=mime,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="visible@example.com, hidden@example.com",
            **auth_header,
        )

        assert response.status_code == 202
        from core.enums import MessageRecipientTypeChoices
        from core.models import Message

        message = Message.objects.get(id=response.json()["message_id"])

        # visible@example.com comes from MIME To: header
        assert message.recipients.filter(
            contact__email="visible@example.com",
            type=MessageRecipientTypeChoices.TO,
        ).exists()

        # hidden@example.com comes from X-Rcpt-To only — added as BCC
        assert message.recipients.filter(
            contact__email="hidden@example.com",
            type=MessageRecipientTypeChoices.BCC,
        ).exists()

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

    @patch(TASK_MOCK)
    def test_stored_blob_is_dkim_signed(self, mock_task, client, auth_header, mailbox):
        """The blob persisted for the outbound message must be DKIM-signed
        with the domain's active key and verifiable against its public key."""
        from core.models import DKIMKey, Message

        private_key, public_key = generate_dkim_key(key_size=1024)
        dkim_key = DKIMKey.objects.create(
            selector="testselector",
            private_key=private_key,
            public_key=public_key,
            key_size=1024,
            is_active=True,
            domain=mailbox.domain,
        )

        response = client.post(
            SUBMIT_URL,
            data=MINIMAL_MIME,
            content_type="message/rfc822",
            HTTP_X_MAIL_FROM=str(mailbox.id),
            HTTP_X_RCPT_TO="attendee@example.com",
            **auth_header,
        )
        assert response.status_code == 202

        message = Message.objects.get(id=response.json()["message_id"])
        stored = message.blob.get_content()

        # Header is present and prepended before the original MIME.
        assert stored.startswith(b"DKIM-Signature:"), stored[:200]

        # Verify the signature cryptographically using the stored public key.
        def get_dns_txt(fqdn, **kwargs):
            if fqdn == b"testselector._domainkey.%s." % mailbox.domain.name.encode():
                return f"v=DKIM1; k=rsa; p={dkim_key.public_key}".encode()
            return None

        assert dkim_verify(stored, dnsfunc=get_dns_txt), (
            "DKIM verification failed on stored blob"
        )

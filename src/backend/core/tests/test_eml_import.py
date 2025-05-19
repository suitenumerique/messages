"""Tests for EML import functionality in the admin interface."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest

from core import factories
from core.models import Mailbox, MailDomain, Message


@pytest.fixture
def admin_user(db):
    """Create a superuser for admin access."""
    return factories.UserFactory(
        email="admin@example.com",
        password="adminpass123",
        full_name="Admin User",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def domain(db):
    """Create a test domain."""
    return MailDomain.objects.create(name="example.com")


@pytest.fixture
def mailbox(db, domain):
    """Create a test mailbox."""
    return Mailbox.objects.create(local_part="test", domain=domain)


@pytest.fixture
def eml_content():
    """Sample EML file content."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Subject
Date: Thu, 1 Jan 2024 12:00:00 +0000
Content-Type: text/plain; charset="UTF-8"

This is a test message body.
"""


@pytest.fixture
def admin_client(client, admin_user):
    """Create an authenticated admin client."""
    client.force_login(admin_user)
    return client


def test_import_button_visibility(admin_client):
    """Test that the import button is visible to admin users."""
    url = reverse("admin:core_message_changelist")
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "Import from EML" in response.content.decode()


def test_import_form_access(admin_client, mailbox):
    """Test access to the import form."""
    url = reverse("admin:core_message_import_eml")
    response = admin_client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Upload Messages from EML file" in content
    assert "EML File" in content
    assert "Mailbox Recipient" in content
    assert str(mailbox) in content  # Check that the mailbox appears in the dropdown


def test_import_form_submission(admin_client, eml_content, mailbox):
    """Test submitting the import form with a valid EML file."""
    url = reverse("admin:core_message_import_eml")

    # Create a test EML file
    eml_file = SimpleUploadedFile(
        "test.eml", eml_content, content_type="message/rfc822"
    )

    # Submit the form
    response = admin_client.post(
        url, {"eml_file": eml_file, "recipient": mailbox.id}, follow=True
    )

    # Check response
    assert response.status_code == 200
    assert (
        f"Successfully processed EML file: test.eml for recipient {mailbox}"
        in response.content.decode()
    )
    # check that the message was created
    assert Message.objects.count() == 1
    message = Message.objects.first()
    assert message.subject == "Test Subject"
    body = message.get_parsed_field("textBody")[0]["content"]
    assert body == "This is a test message body.\n"
    assert message.sender.email == "sender@example.com"
    assert message.recipients.get().contact.email == "recipient@example.com"


# def test_import_form_invalid_file(admin_client, mailbox):
#    """Test submitting the import form with an invalid file."""
#    url = reverse('admin:core_message_import_eml')

#    # Create an invalid file (not EML)
#    invalid_file = SimpleUploadedFile(
#        "test.txt",
#        b"Not an EML file",
#        content_type="text/plain"
#    )

#    # Submit the form
#    response = admin_client.post(
#        url,
#        {
#            'eml_file': invalid_file,
#            'recipient': mailbox.id
#        },
#        follow=True
#    )

#    # Check response
#    assert response.status_code == 200
#    # The form should still be displayed with an error
#    assert 'Import Messages from EML file' in response.content.decode()


def test_import_form_no_file(admin_client, mailbox):
    """Test submitting the import form without a file."""
    url = reverse("admin:core_message_import_eml")

    # Submit the form without a file but with recipient
    response = admin_client.post(url, {"recipient": mailbox.id}, follow=True)

    # Check response
    assert response.status_code == 200
    # The form should still be displayed with an error
    assert "Upload Messages from EML file" in response.content.decode()


def test_import_form_no_recipient(admin_client, eml_content):
    """Test submitting the import form without a recipient."""
    url = reverse("admin:core_message_import_eml")

    # Create a test EML file
    eml_file = SimpleUploadedFile(
        "test.eml", eml_content, content_type="message/rfc822"
    )

    # Submit the form without recipient
    response = admin_client.post(url, {"eml_file": eml_file}, follow=True)

    # Check response
    assert response.status_code == 200
    # The form should still be displayed with an error
    assert "Upload Messages from EML file" in response.content.decode()
    assert "This field is required" in response.content.decode()

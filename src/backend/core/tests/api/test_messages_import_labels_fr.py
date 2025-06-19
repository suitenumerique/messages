"""Tests for French mbox import with labels and flags via API."""

# pylint: disable=redefined-outer-name,R0801

import hashlib

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from core import models
from core.factories import MailboxFactory, UserFactory
from core.models import Blob

IMPORT_FILE_URL = "/api/v1.0/import/file/"


@pytest.fixture
def api_client():
    """Create an API client."""
    return APIClient()


@pytest.fixture
def user():
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def mailbox(user):
    """Create a test mailbox with user access."""
    mailbox = MailboxFactory()
    mailbox.accesses.create(user=user, role=models.MailboxRoleChoices.ADMIN)
    return mailbox


@pytest.fixture
def authenticated_client(api_client, user):
    """Create an authenticated API client."""
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def mbox_file_path():
    """Get the path to the test French mbox file."""
    return (
        "core/tests/resources/Tous les messages, y compris ceux du dossier Spam .mbox"
    )


def upload_mbox_file(client, mbox_file_path, mailbox):
    """Helper function to upload mbox file via API."""
    with open(mbox_file_path, "rb") as f:
        mbox_content = f.read()

    blob = Blob.objects.create(
        raw_content=mbox_content,
        type="application/mbox",
        size=len(mbox_content),
        mailbox=mailbox,
        sha256=hashlib.sha256(mbox_content).hexdigest(),
    )

    response = client.post(
        IMPORT_FILE_URL,
        {"blob": blob.id, "recipient": str(mailbox.id)},
        format="multipart",
    )
    return response


@pytest.mark.django_db
def test_import_french_mbox_with_labels_and_flags(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that French mbox import correctly creates labels and sets flags."""
    # check db is empty
    assert not models.Message.objects.exists()

    # Import the mbox file via API
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)

    # Check that the import was accepted
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["type"] == "mbox"
    assert "task_id" in response.data

    # Wait for the task to complete (in a real scenario, you'd poll the task status)
    # For now, we'll assume the task completes and check the results

    # Check that messages were created
    messages = models.Message.objects.filter(thread__accesses__mailbox=mailbox)
    assert messages.count() > 0

    # Test specific message with "Boîte de réception,Non lus,Conseil municipal" labels
    unread_message = messages.filter(is_unread=True).first()
    assert unread_message is not None

    # Check that "Conseil municipal" label was created
    conseil_label = models.Label.objects.filter(
        name="Conseil municipal", mailbox=mailbox
    ).first()
    assert conseil_label is not None
    convocation_message = messages.get(
        subject="Convocation au conseil municipal du 25 juin"
    )
    assert convocation_message.is_unread
    assert conseil_label in convocation_message.thread.labels.all()

    # Test sent message with "Messages envoyés" labels is a flag and marked as unread
    sent_message = messages.filter(is_sender=True).first()
    assert sent_message is not None
    assert not sent_message.is_unread  # Sent messages should not be unread

    # Check that "Corbeille" label is now a flag
    assert models.Message.objects.filter(is_trashed=True).exists()
    assert not models.Label.objects.filter(name="Corbeille").exists()

    # Test draft message with "Brouillons" label
    draft_message = messages.filter(is_draft=True).first()
    assert draft_message is not None
    assert not draft_message.is_unread  # Drafts should not be unread
    assert not models.Label.objects.filter(name="Brouillons").exists()

    # Test starred message with "Favoris" label
    starred_message = messages.filter(is_starred=True).first()
    assert starred_message is not None
    assert not models.Label.objects.filter(name="Favoris").exists()

    # Test archived message with "Messages archivés" label
    assert messages.filter(is_archived=True).exists()
    assert not models.Label.objects.filter(name="Messages archivés").exists()

    # Test hierarchical labels
    hierarchical_label = models.Label.objects.filter(
        name__startswith="Petite enfance", mailbox=mailbox
    ).first()
    assert hierarchical_label is not None
    assert models.Label.objects.filter(name="Petite enfance", mailbox=mailbox).exists()
    assert models.Label.objects.filter(
        name="Petite enfance/Cantine", mailbox=mailbox
    ).exists()


@pytest.mark.django_db
def test_french_gmail_system_labels_are_ignored(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that French Gmail system labels are not created as user labels."""
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    # These French Gmail system labels should not be created
    ignored_labels = [
        "Boîte de réception",
        "Promotions",
        "Social",
        "Inbox",
        "Messages envoyés",
        "Messages archivés",
        "Brouillons",
        "Corbeille",
        "Favoris",
    ]
    for label_name in ignored_labels:
        label = models.Label.objects.filter(name=label_name, mailbox=mailbox).first()
        assert label is None, f"Label '{label_name}' should not be created"


@pytest.mark.django_db
def test_french_read_unread_labels_set_correctly(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that French read/unread status is set correctly based on Gmail labels."""
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    messages = models.Message.objects.filter(thread__accesses__mailbox=mailbox)

    # Check that we have both read and unread messages
    unread_messages = messages.filter(is_unread=True)
    read_messages = messages.filter(is_unread=False)

    assert unread_messages.count() > 0
    assert read_messages.count() > 0


@pytest.mark.django_db
def test_french_special_cases_for_sent_and_draft_messages(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that French sent and draft messages are automatically marked as read."""
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    # Sent messages should not be unread
    sent_messages = models.Message.objects.filter(
        thread__accesses__mailbox=mailbox, is_sender=True
    )
    for message in sent_messages:
        assert not message.is_unread

    # Draft messages should not be unread
    draft_messages = models.Message.objects.filter(
        thread__accesses__mailbox=mailbox, is_draft=True
    )
    for message in draft_messages:
        assert not message.is_unread


@pytest.mark.django_db
def test_french_hierarchical_labels_are_created_correctly(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that French hierarchical labels are created with proper structure."""
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    # Check that parent labels are created
    parent_label = models.Label.objects.filter(
        name="Petite enfance", mailbox=mailbox
    ).first()
    assert parent_label is not None
    assert parent_label.parent_name is None
    assert parent_label.depth == 0

    # Check that child labels are created
    child_label = models.Label.objects.filter(
        name="Petite enfance/Cantine", mailbox=mailbox
    ).first()
    assert child_label is not None
    assert child_label.parent_name == "Petite enfance"
    assert child_label.depth == 1

    # Check for another hierarchical label
    ecole_label = models.Label.objects.filter(
        name__startswith="Petite enfance/École", mailbox=mailbox
    ).first()
    assert ecole_label is not None
    assert ecole_label.parent_name == "Petite enfance"
    assert ecole_label.depth == 1


@pytest.mark.django_db
def test_french_thread_stats_are_updated_correctly(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that French thread statistics are updated after flag changes."""
    # check db is empty
    assert not models.Message.objects.exists()
    assert not models.Thread.objects.exists()

    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    messages_unread = models.Message.objects.filter(
        thread__accesses__mailbox=mailbox, is_unread=True
    )
    assert messages_unread.count() > 0

    # check that thread stats are updated
    for message in messages_unread:
        assert message.thread.has_unread
        assert message.thread.has_messages


@pytest.mark.django_db
def test_french_api_authentication_required(api_client, mbox_file_path, mailbox):
    """Test that API authentication is required for French mbox import."""
    with open(mbox_file_path, "rb") as f:
        mbox_content = f.read()

    blob = Blob.objects.create(
        raw_content=mbox_content,
        type="application/mbox",
        size=len(mbox_content),
        mailbox=mailbox,
        sha256=hashlib.sha256(mbox_content).hexdigest(),
    )

    response = api_client.post(
        IMPORT_FILE_URL,
        {"blob": blob.id, "recipient": str(mailbox.id)},
        format="multipart",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_french_mailbox_access_required(api_client, mbox_file_path, mailbox):
    """Test that user must have access to mailbox for French mbox import."""
    # Create user without mailbox access
    other_user = UserFactory()
    api_client.force_authenticate(user=other_user)

    with open(mbox_file_path, "rb") as f:
        mbox_content = f.read()

    blob = Blob.objects.create(
        raw_content=mbox_content,
        type="application/mbox",
        size=len(mbox_content),
        mailbox=mailbox,
        sha256=hashlib.sha256(mbox_content).hexdigest(),
    )

    response = api_client.post(
        IMPORT_FILE_URL,
        {"blob": blob.id, "recipient": str(mailbox.id)},
        format="multipart",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_french_utf8_encoded_labels_are_handled_correctly(
    authenticated_client, mbox_file_path, mailbox
):
    """Test that UTF-8 encoded French labels are properly decoded and handled."""
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    # Check that UTF-8 encoded labels are properly decoded
    # "Boîte de réception" should be ignored (system label)
    assert not models.Label.objects.filter(name="Boîte de réception").exists()

    # "Non lus" should be handled as read/unread status, not as a label
    assert not models.Label.objects.filter(name="Non lus").exists()

    # "Ouvert" should be handled as read/unread status, not as a label
    assert not models.Label.objects.filter(name="Ouvert").exists()


@pytest.mark.django_db
def test_french_mixed_language_labels(authenticated_client, mbox_file_path, mailbox):
    """Test that mixed French/English labels are handled correctly."""
    response = upload_mbox_file(authenticated_client, mbox_file_path, mailbox)
    assert response.status_code == status.HTTP_202_ACCEPTED

    # Check that French labels are created as user labels
    conseil_label = models.Label.objects.filter(
        name="Conseil municipal", mailbox=mailbox
    ).first()
    assert conseil_label is not None

    # Check that hierarchical French labels are created
    petite_enfance_label = models.Label.objects.filter(
        name="Petite enfance", mailbox=mailbox
    ).first()
    assert petite_enfance_label is not None

    # Check that French system labels are mapped to flags
    assert models.Message.objects.filter(is_draft=True).exists()  # "Brouillons"
    assert models.Message.objects.filter(is_sender=True).exists()  # "Messages envoyés"
    assert models.Message.objects.filter(
        is_archived=True
    ).exists()  # "Messages archivés"
    assert models.Message.objects.filter(is_trashed=True).exists()  # "Corbeille"

"""Fixtures for tests in the messages core api application"""
# pylint: disable=redefined-outer-name

import json

import pytest
from rest_framework.test import APIClient

from core import enums, factories

# Template test data
MESSAGE_TEMPLATE_RAW_DATA = [
    {
        "id": "e209581a-3689-4d7e-99c3-a2bfa8bd4530",
        "type": "paragraph",
        "props": {
            "textColor": "default",
            "backgroundColor": "default",
            "textAlignment": "left",
        },
        "content": [{"type": "text", "text": "----", "styles": {}}],
        "children": [],
    },
    {
        "id": "61380a2d-74d0-4a3c-b5c5-135e6b3e2ae9",
        "type": "paragraph",
        "props": {
            "textColor": "default",
            "backgroundColor": "default",
            "textAlignment": "left",
        },
        "content": [
            {
                "type": "template-variable",
                "props": {"value": "full_name", "label": "Nom complet"},
            },
            {"type": "text", "text": " - Mairie de Brigny", "styles": {}},
        ],
        "children": [],
    },
    {
        "id": "bcd7b650-0c1e-4239-bf61-7cc2702838e8",
        "type": "paragraph",
        "props": {
            "textColor": "default",
            "backgroundColor": "default",
            "textAlignment": "left",
        },
        "content": [],
        "children": [],
    },
]

# Convert to JSON string for API requests
MESSAGE_TEMPLATE_RAW_DATA_JSON = json.dumps(MESSAGE_TEMPLATE_RAW_DATA)


@pytest.fixture
def mailbox():
    """Create a mailbox."""
    return factories.MailboxFactory()


@pytest.fixture
def thread(mailbox):
    """Create a thread for a mailbox."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox,
        thread=thread,
        role=enums.ThreadAccessRoleChoices.EDITOR,
    )
    return thread


@pytest.fixture
def message(thread):
    """Create a message for a thread."""
    return factories.MessageFactory(
        thread=thread, read_at=None, raw_mime=b"raw email content"
    )


@pytest.fixture
def other_user():
    """Create a user without mailbox access."""
    return factories.UserFactory()


@pytest.fixture
def mailbox_access(mailbox):
    """Create a mailbox access."""
    return factories.MailboxAccessFactory(mailbox=mailbox)


# Add an api_client fixture
@pytest.fixture
def api_client():
    """Provide an instance of the API client for tests."""
    return APIClient()

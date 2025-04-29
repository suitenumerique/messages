"""Fixtures for tests in the messages core api application"""
# pylint: disable=redefined-outer-name

import pytest
from rest_framework.test import APIClient

from core import factories


@pytest.fixture
def mailbox():
    """Create a mailbox."""
    return factories.MailboxFactory()


@pytest.fixture
def thread(mailbox):
    """Create a thread for a mailbox."""
    return factories.ThreadFactory(mailbox=mailbox)


@pytest.fixture
def message(thread):
    """Create a message for a thread."""
    return factories.MessageFactory(thread=thread, read_at=None)


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
    return APIClient()

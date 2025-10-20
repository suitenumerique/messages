"""Fixtures for tests in the messages core application"""

from unittest import mock

import dramatiq
import pytest

USER = "user"
TEAM = "team"
VIA = [USER, TEAM]


@pytest.fixture(name="worker_broker")
def fixture_worker_broker():
    """Fixture that provides a clean StubBroker for testing."""
    broker = dramatiq.get_broker()
    broker.flush_all()
    return broker


@pytest.fixture(name="worker")
def fixture_worker(worker_broker):
    """Fixture that provides a Dramatiq worker for testing."""
    worker = dramatiq.Worker(worker_broker, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()


@pytest.fixture
def mock_user_teams():
    """Mock for the "teams" property on the User model."""
    with mock.patch(
        "core.models.User.teams", new_callable=mock.PropertyMock
    ) as mock_teams:
        yield mock_teams

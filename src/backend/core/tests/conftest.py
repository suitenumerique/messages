"""Fixtures for tests in the messages core application"""

from unittest import mock

import pytest

USER = "user"
TEAM = "team"
VIA = [USER, TEAM]


@pytest.fixture
def mock_user_teams():
    """Mock for the "teams" property on the User model."""
    with mock.patch(
        "core.models.User.teams", new_callable=mock.PropertyMock
    ) as mock_teams:
        yield mock_teams


# @pytest.fixture
# @pytest.mark.django_db
# def create_testdomain():
#     """Create the TESTDOMAIN."""
#     from core import models
#     models.MailDomain.objects.get_or_create(
#         name=settings.MESSAGES_TESTDOMAIN,
#         defaults={
#             "oidc_autojoin": True
#         }
#     )

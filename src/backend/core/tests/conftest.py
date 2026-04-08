"""Fixtures for tests in the messages core application"""

from unittest import mock

from django.db.models import F

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


@pytest.fixture(autouse=True)
def _assert_user_event_thread_invariant(request):
    """Turn every DB-enabled test into a sentinel for the UserEvent invariant.

    ``UserEvent.thread`` is a denormalization of ``UserEvent.thread_event.thread``
    (see the model docstring at ``core/models.py``). The denormalization exists
    for query-plan reasons — the ``Exists(...)`` annotations that power the
    mention filters in ``ThreadViewSet.get_queryset`` rely on filtering
    ``UserEvent`` by ``thread`` directly without a JOIN on ``ThreadEvent``.
    That makes the ``thread_id == thread_event.thread_id`` equality a hard
    invariant: any divergence silently corrupts the mention UX.

    Python writes through ``save()`` don't help here because the hot path uses
    ``bulk_create`` (mention signal) and plain ``update()`` (thread split).
    Rather than duplicate a check on every call site, we let the test suite
    catch regressions by scanning for violators after each DB-enabled test.
    The query is a single index-friendly ``EXCLUDE`` with no JOIN amplification
    so the overhead is negligible.
    """
    yield
    # Skip for tests that don't hit the database — avoids
    # ``Database access not allowed`` errors on pure unit tests.
    if not any(request.node.iter_markers("django_db")):
        return

    # Imported late so conftest import does not pull Django models before
    # settings are configured.
    from core.models import UserEvent  # pylint: disable=import-outside-toplevel

    violators = UserEvent.objects.exclude(thread_id=F("thread_event__thread_id"))
    count = violators.count()
    assert count == 0, (
        f"UserEvent invariant broken: {count} row(s) where "
        "thread_id != thread_event.thread_id"
    )


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

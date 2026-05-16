"""Fixtures for tests in the messages core application"""

# pylint: disable=import-outside-toplevel

import os
from unittest import mock

from django.db.models import F

import pytest

USER = "user"
TEAM = "team"
VIA = [USER, TEAM]


@pytest.fixture
def redis_cache(settings):
    """Point ``CACHES['default']`` at the real Redis service for one test.

    Tests that exercise the ``django_redis``-backed primitives in
    ``coalescer`` and ``blob_gc`` need a running Redis: the LocMem
    fallback path was deliberately dropped because it can't deliver
    SADD/SPOP atomicity across workers. CI's ``backend-dev`` container
    already declares ``redis`` as a ``depends_on``, so the service is
    reachable at ``redis:6379`` (overridable via ``REDIS_URL``).

    Coalescer / blob_gc call ``get_redis_client().sadd(...)`` directly
    with hard-coded keys, so Django's ``KEY_PREFIX`` doesn't isolate
    them. We isolate parallel xdist workers by routing each
    worker to a distinct Redis DB (``gw0`` → DB 1, ``gw1`` → DB 2, …;
    non-xdist runs land on DB 1). ``flushdb`` at setup and teardown
    keeps the slot clean. Default Redis ships with 16 DBs, enough for
    typical xdist sizes; if you push past that, add
    ``@pytest.mark.xdist_group("redis")`` to serialize.

    Pair with ``@pytest.mark.redis`` so the test can be excluded via
    ``pytest -m "not redis"`` when Redis isn't available locally.
    """
    base_url = os.environ.get("REDIS_URL", "redis://redis:6379").rstrip("/")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    db_index = int(worker[2:]) + 1 if worker.startswith("gw") else 1
    location = f"{base_url}/{db_index}"

    settings.CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": location,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        },
    }

    from core.utils import get_redis_client

    client = get_redis_client()
    client.flushdb()

    yield client

    try:
        client.flushdb()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


@pytest.fixture(scope="session", autouse=True)
def ensure_storage_buckets():
    """Create any missing S3 buckets needed by tests (session-scoped, autouse).

    ``head_bucket`` raises ``ClientError`` on 404 (boto3 doesn't expose a
    ``NoSuchBucket`` class for HEAD); other status codes are real failures
    and propagate so config problems aren't silently masked.
    """
    from django.core.files.storage import storages

    from botocore.exceptions import ClientError

    for storage_name in ("message-imports", "message-blobs"):
        if storage_name not in storages.backends:
            continue
        storage = storages[storage_name]
        if not hasattr(storage, "bucket"):
            continue
        client = storage.bucket.meta.client
        bucket_name = storage.bucket.name
        try:
            client.head_bucket(Bucket=bucket_name)
        except ClientError as e:
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = e.response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchBucket"}:
                client.create_bucket(Bucket=bucket_name)
            else:
                raise


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

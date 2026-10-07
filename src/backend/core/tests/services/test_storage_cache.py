"""Tests for the service-layer storage-usage cache."""

from django.core.cache import cache

import pytest

from core import factories, models
from core.services.storage import (
    compute_mailbox_storage_used,
    get_mailbox_storage_used,
    invalidate_mailbox_storage,
)
from core.services.trashbin import empty_trashbin

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _mailbox_with_message(**flags):
    user = factories.UserFactory()
    mailbox = factories.MailboxFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox, user=user, role=models.MailboxRoleChoices.ADMIN
    )
    contact = factories.ContactFactory(mailbox=mailbox)
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=models.ThreadAccessRoleChoices.EDITOR
    )
    factories.MessageFactory(
        thread=thread, sender=contact, raw_mime=b"x" * 500, **flags
    )
    return user, mailbox, contact


def test_get_caches_until_invalidated(settings):
    """The cached getter holds a value until explicitly invalidated."""
    settings.STORAGE_USAGE_CACHE_TTL = 60
    user, mailbox, contact = _mailbox_with_message()

    first = get_mailbox_storage_used(mailbox)
    assert first == compute_mailbox_storage_used(mailbox)

    # A newly added message changes the true value, but the cache still holds.
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
    factories.MessageFactory(thread=thread, sender=contact, raw_mime=b"y" * 5000)
    live = compute_mailbox_storage_used(mailbox)
    assert live > first
    assert get_mailbox_storage_used(mailbox) == first  # still cached, stale

    invalidate_mailbox_storage(mailbox)
    assert get_mailbox_storage_used(mailbox) == live  # recomputed


def test_ttl_zero_disables_cache(settings):
    """TTL=0 bypasses the cache and always recomputes."""
    settings.STORAGE_USAGE_CACHE_TTL = 0
    user, mailbox, contact = _mailbox_with_message()

    first = get_mailbox_storage_used(mailbox)
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(mailbox=mailbox, thread=thread)
    factories.MessageFactory(thread=thread, sender=contact, raw_mime=b"z" * 5000)

    assert get_mailbox_storage_used(mailbox) > first  # no caching


def test_empty_trashbin_invalidates_cache(settings):
    """Emptying the trashbin drops the cached usage so the gauge updates."""
    settings.STORAGE_USAGE_CACHE_TTL = 60
    settings.TRASHBIN_ALLOW_EMPTY = "admins"
    user, mailbox, contact = _mailbox_with_message(is_trashed=True)

    before = get_mailbox_storage_used(mailbox)  # primes the cache
    assert before > 0

    deleted = empty_trashbin(mailbox, "trashed", user)
    assert deleted == 1

    # Without invalidation this would still read `before`; the emptied trashbin
    # is reflected immediately.
    assert get_mailbox_storage_used(mailbox) < before

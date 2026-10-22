"""Tests for ``Thread.is_spam`` derivation via ``update_stats``."""

import pytest

from core import factories

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "flags,expected",
    [
        ([False, False], False),
        ([True, False], True),
        ([False, True], True),
        ([True, True], True),
    ],
)
def test_thread_is_spam_when_any_message_is(flags, expected):
    """Spam is a conversation property: one spam message is enough."""
    thread = factories.ThreadFactory()
    for is_spam in flags:
        factories.MessageFactory(thread=thread, is_spam=is_spam)

    thread.update_stats()

    assert thread.is_spam is expected

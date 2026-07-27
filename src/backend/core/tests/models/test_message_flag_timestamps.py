"""Tests for the timestamps ``Message.save`` keeps in lockstep with the flags."""

from datetime import timedelta

from django.utils import timezone

import pytest

from core import factories

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "flag,field",
    [
        ("is_trashed", "trashed_at"),
        ("is_spam", "trashed_at"),
        ("is_archived", "archived_at"),
    ],
)
def test_setting_a_flag_dates_it(flag, field):
    """A flag set without a date is dated at save, even through update_fields."""
    message = factories.MessageFactory()
    assert getattr(message, field) is None

    setattr(message, flag, True)
    message.save(update_fields=[flag])

    message.refresh_from_db()
    assert getattr(message, field) > timezone.now() - timedelta(minutes=1)


def test_an_existing_date_is_kept():
    """The bin entry date survives a save: the retention clock never restarts."""
    entered = timezone.now() - timedelta(days=20)
    message = factories.MessageFactory(is_trashed=True, trashed_at=entered)

    message.is_spam = True
    message.save(update_fields=["is_spam"])

    message.refresh_from_db()
    assert message.trashed_at == entered


@pytest.mark.parametrize(
    "flags,expected_in_bin",
    [
        ({"is_spam": False}, True),
        ({"is_spam": False, "is_trashed": False}, False),
    ],
)
def test_the_bin_date_lasts_as_long_as_either_flag(flags, expected_in_bin):
    """Leaving one half of the trashbin keeps the date, leaving both clears it."""
    message = factories.MessageFactory(is_trashed=True, is_spam=True)

    for flag, value in flags.items():
        setattr(message, flag, value)
    message.save(update_fields=list(flags))

    message.refresh_from_db()
    assert (message.trashed_at is not None) is expected_in_bin


def test_unarchiving_clears_the_date():
    """archived_at only holds while the message is archived."""
    message = factories.MessageFactory(is_archived=True)
    assert message.archived_at is not None

    message.is_archived = False
    message.save(update_fields=["is_archived"])

    message.refresh_from_db()
    assert message.archived_at is None

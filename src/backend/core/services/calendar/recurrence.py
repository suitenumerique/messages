"""Occurrences of a recurring VEVENT."""

from datetime import datetime, timezone


def recurrence_key(value):
    """Return a key that identifies an occurrence by its original start.

    Two date-times with a timezone give the same key when they are the same
    instant, in any timezone. A date and a date-time without timezone compare
    by value. Values of different kinds never give the same key.
    """
    if not isinstance(value, datetime):
        return ("date", value)
    if value.tzinfo is None:
        return ("floating", value)
    return ("instant", value.astimezone(timezone.utc))

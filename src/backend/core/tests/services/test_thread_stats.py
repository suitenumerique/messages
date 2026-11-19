"""Tests for the thread stats service."""

import pytest

from core.services.thread_stats import (
    ANNOTATED_STATS_FIELDS,
    COLUMN_STATS_FIELDS,
    SPECIAL_STATS_FIELDS,
    STATS_FIELDS,
    UNREAD_SUFFIX,
    InvalidStatsFieldsError,
    parse_stats_fields,
)

# Every known base field, plus thread columns the parser must refuse, with
# and without the unread suffix.
CANDIDATE_FIELDS = sorted(
    f"{base}{suffix}"
    for base in (
        COLUMN_STATS_FIELDS
        | frozenset(ANNOTATED_STATS_FIELDS)
        | SPECIAL_STATS_FIELDS
        | {"is_spam", "is_trashed", "has_unread"}
    )
    for suffix in ("", UNREAD_SUFFIX)
)


def test_stats_fields_are_accepted_together():
    """The values the API schema advertises can be requested at once."""
    assert parse_stats_fields(",".join(STATS_FIELDS)) == STATS_FIELDS


@pytest.mark.parametrize("field", CANDIDATE_FIELDS)
def test_stats_fields_match_the_parser(field):
    """The schema advertises a field if and only if the parser accepts it."""
    try:
        parse_stats_fields(field)
    except InvalidStatsFieldsError:
        accepted = False
    else:
        accepted = True

    assert accepted == (field in STATS_FIELDS)

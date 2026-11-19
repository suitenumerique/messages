"""Thread counts behind the sidebar badges.

Shared by ``GET /threads/stats/`` (one set of counts for a filtered thread
queryset) and ``GET /labels/stats/`` (one set of counts per label of a
mailbox), so that every badge measures threads the same way.
"""

from django.db.models import Count, Exists, OuterRef, Q

from core import enums, models

# Thread columns that can be counted as is, or with the ``_unread`` suffix.
# ``is_spam`` is deliberately absent: stats querysets exclude spam by
# default, so the counter could only ever return 0 — or, with an explicit
# ``is_spam=1``, a duplicate of ``all``. Folders scope their counts through
# that query parameter instead (the Spam folder asks for
# ``is_spam=1&stats_fields=all_unread``).
COLUMN_STATS_FIELDS = frozenset(
    {
        "has_trashed",
        "has_archived",
        "has_draft",
        "has_attachments",
        "has_sender",
        "has_active",
        "has_delivery_failed",
        "has_delivery_pending",
        "has_messages",
    }
)

# Fields backed by a per-user annotation (see ``thread_state_annotations``),
# mapped to that annotation.
ANNOTATED_STATS_FIELDS = {
    "has_starred": "_has_starred",
    "has_unread_mention": "_has_unread_mention",
    "has_mention": "_has_mention",
    "has_assigned_to_me": "_has_assigned_to_me",
    "has_unassigned": "_has_unassigned",
}

# Annotated fields whose ``_unread`` variant is either already exposed
# (``has_unread_mention``) or meaningless.
NO_UNREAD_VARIANT_FIELDS = frozenset(ANNOTATED_STATS_FIELDS) - {"has_starred"}

SPECIAL_STATS_FIELDS = frozenset({"all", "all_unread"})

UNREAD_SUFFIX = "_unread"

# Every value ``stats_fields`` accepts, as the API schema advertises them.
STATS_FIELDS = sorted(
    SPECIAL_STATS_FIELDS
    | COLUMN_STATS_FIELDS
    | frozenset(ANNOTATED_STATS_FIELDS)
    | {
        f"{field}{UNREAD_SUFFIX}"
        for field in COLUMN_STATS_FIELDS | frozenset(ANNOTATED_STATS_FIELDS)
        if field not in NO_UNREAD_VARIANT_FIELDS
    }
)


class InvalidStatsFieldsError(ValueError):
    """Raised when the requested ``stats_fields`` cannot be counted."""


def thread_state_annotations(user, mailbox_id):
    """Annotations describing the state of a thread for ``user``.

    They back both the boolean filters of the thread list and the stats
    counters, so a badge and the folder it points at always agree. When
    ``mailbox_id`` is given, ``_has_unread`` reads the read state of that
    mailbox's access: the queryset must already be joined on it (e.g.
    ``Mailbox.threads_viewer``).
    """
    user_events = models.UserEvent.objects.filter(thread=OuterRef("pk"), user=user)
    return {
        "_has_unread": models.ThreadAccess.thread_unread_filter(user, mailbox_id),
        "_has_starred": models.ThreadAccess.thread_starred_filter(user, mailbox_id),
        "_has_unread_mention": Exists(
            user_events.filter(
                type=enums.UserEventTypeChoices.MENTION, read_at__isnull=True
            )
        ),
        "_has_mention": Exists(
            user_events.filter(type=enums.UserEventTypeChoices.MENTION)
        ),
        "_has_assigned_to_me": Exists(
            user_events.filter(type=enums.UserEventTypeChoices.ASSIGN)
        ),
        "_has_unassigned": ~Exists(
            models.UserEvent.objects.filter(
                thread=OuterRef("pk"), type=enums.UserEventTypeChoices.ASSIGN
            )
        ),
    }


def parse_stats_fields(raw_fields):
    """Split and validate a comma-separated ``stats_fields`` value.

    Raises ``InvalidStatsFieldsError`` with a user-facing message.
    """
    if not raw_fields:
        raise InvalidStatsFieldsError("Missing 'stats_fields' query parameter.")

    fields = [field.strip() for field in raw_fields.split(",")]
    for field in fields:
        if field in SPECIAL_STATS_FIELDS:
            continue
        if field.endswith(UNREAD_SUFFIX):
            base_field = field.removesuffix(UNREAD_SUFFIX)
            if (
                base_field not in COLUMN_STATS_FIELDS
                and base_field not in ANNOTATED_STATS_FIELDS
            ):
                raise InvalidStatsFieldsError(
                    f"Invalid base field in '{field}': {base_field}"
                )
            if base_field in NO_UNREAD_VARIANT_FIELDS:
                raise InvalidStatsFieldsError(
                    f"Invalid field requested in stats_fields: {field}"
                )
        elif field not in COLUMN_STATS_FIELDS and field not in ANNOTATED_STATS_FIELDS:
            raise InvalidStatsFieldsError(
                f"Invalid field requested in stats_fields: {field}"
            )
    return fields


def _field_condition(field):
    """Condition a thread must meet to be counted in ``field``."""
    if field in ANNOTATED_STATS_FIELDS:
        return Q(**{ANNOTATED_STATS_FIELDS[field]: True})
    return Q(**{field: True})


def _aggregations(fields):
    """``Count`` per field, keyed ``count_<field>``.

    The prefix keeps aggregate aliases from clashing with the thread columns
    they count (e.g. ``has_trashed``).
    """
    unread_condition = Q(_has_unread=True)
    aggregations = {}
    for field in fields:
        if field == "all":
            condition = None
        elif field == "all_unread":
            condition = unread_condition
        elif field.endswith(UNREAD_SUFFIX) and field not in ANNOTATED_STATS_FIELDS:
            condition = (
                _field_condition(field.removesuffix(UNREAD_SUFFIX)) & unread_condition
            )
        else:
            condition = _field_condition(field)
        aggregations[f"count_{field}"] = Count("pk", filter=condition)
    return aggregations


def _to_counts(row, fields):
    return {field: row.get(f"count_{field}") or 0 for field in fields}


def count_threads(queryset, fields):
    """Count the threads of ``queryset`` for each of ``fields``.

    ``queryset`` must carry the ``thread_state_annotations``.
    """
    return _to_counts(queryset.aggregate(**_aggregations(fields)), fields)


def count_threads_by_label(user, mailbox, fields):
    """Count, for each label of ``mailbox``, its threads for each of ``fields``.

    The scope is the default view of a label folder: threads the mailbox
    can see, spam and trash excluded, and only the threads carrying the
    label itself (a parent label does not count its children). A single
    grouped query replaces one ``/threads/stats/?label_slug=`` per label.
    Labels without any thread in that scope are absent from the result.
    """
    rows = (
        mailbox.threads_viewer.filter(is_trashed=False, is_spam=False)
        .annotate(**thread_state_annotations(user, mailbox.id))
        # A thread may also carry labels of the other mailboxes it is shared
        # with: only group on this mailbox's ones.
        .filter(labels__mailbox=mailbox)
        .order_by()
        .values("labels__id")
        .annotate(**_aggregations(fields))
    )
    return {str(row["labels__id"]): _to_counts(row, fields) for row in rows}

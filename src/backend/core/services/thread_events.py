"""Service layer for ThreadEvent operations.

The service is the single entry point for creating, updating,
and reverting assignment / mention state;
viewsets and ``ModelAdmin`` call into it directly.
"""

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core import enums, models

logger = logging.getLogger(__name__)


# Window during which an UNASSIGN by the same author that targets a user
# freshly assigned via a recent ASSIGN ThreadEvent is treated as an "undo":
# the offending user is stripped from the original ASSIGN event (the event
# is deleted if it becomes empty) and no UNASSIGN event is emitted. Mirrors
# a classic "undo a misclick" pattern and avoids cluttering the timeline
# with back-to-back noise.
UNDO_WINDOW_SECONDS = 120


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_and_dedupe_user_ids(users_data, *, context):
    """Extract unique, well-formed UUIDs from a list of user-shape dicts.

    Skips entries with missing or malformed ``id`` fields.
    ``context`` is a free-form string included in the log message.
    """
    seen_user_ids = set()
    unique_user_ids = []
    for entry in users_data or []:
        raw_id = entry.get("id")
        if not raw_id:
            continue
        try:
            user_id = uuid.UUID(raw_id)
        except (ValueError, AttributeError):
            logger.warning(
                "Skipping user with invalid UUID '%s' in context %s",
                raw_id,
                context,
            )
            continue
        if user_id not in seen_user_ids:
            seen_user_ids.add(user_id)
            unique_user_ids.append(user_id)
    return unique_user_ids


def _validate_user_ids_with_access(thread, users_data, *, context):
    """Keep only user IDs that have any ThreadAccess on ``thread``.

    Used by MENTION: posting an internal comment makes sense for any user
    who can read the thread, so VIEWER access is enough. Silently drops
    and logs users that do not match — this is a filter, not a validator,
    so callers do not need to handle a raised exception.
    """
    unique_user_ids = _parse_and_dedupe_user_ids(users_data, context=context)
    if not unique_user_ids:
        return set()

    valid_user_ids = set(
        models.ThreadAccess.objects.filter(
            thread=thread,
            mailbox__accesses__user_id__in=unique_user_ids,
        ).values_list("mailbox__accesses__user_id", flat=True)
    )

    for user_id in unique_user_ids:
        if user_id not in valid_user_ids:
            logger.warning(
                "Skipping user %s in context %s: user not found or no thread access",
                user_id,
                context,
            )

    return valid_user_ids


def _validate_user_ids_with_edit_rights(thread, users_data, *, context):
    """Keep only user IDs that have full edit rights on ``thread``.

    Used by ASSIGN: callers (viewset, admin) enforce the same rule
    upstream, so this is a defence-in-depth filter for paths that may
    reach the service with stale data.
    """
    unique_user_ids = _parse_and_dedupe_user_ids(users_data, context=context)
    if not unique_user_ids:
        return set()

    valid_user_ids = set(
        models.ThreadAccess.objects.editor_user_ids(thread.id, user_ids=unique_user_ids)
    )

    for user_id in unique_user_ids:
        if user_id not in valid_user_ids:
            logger.warning(
                "Skipping user %s in context %s: "
                "user does not have edit rights on the thread",
                user_id,
                context,
            )

    return valid_user_ids


def _create_user_event_assigns(thread_event, thread, assignees_data):
    """Create UserEvent ASSIGN rows for the given assignees.

    Assumes upstream validation has narrowed ``assignees_data`` to users
    who hold editor rights on ``thread``. ``ignore_conflicts=True`` lets
    the partial UniqueConstraint on (user, thread) WHERE type=ASSIGN
    absorb concurrent ASSIGN requests racing to insert the same row.
    """
    valid_user_ids = _validate_user_ids_with_edit_rights(
        thread, assignees_data, context=str(thread_event.id)
    )
    if not valid_user_ids:
        return []

    user_events = [
        models.UserEvent(
            user_id=user_id,
            thread=thread,
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.ASSIGN,
        )
        for user_id in valid_user_ids
    ]
    models.UserEvent.objects.bulk_create(user_events, ignore_conflicts=True)
    logger.info(
        "Created %d UserEvent ASSIGN(s) for ThreadEvent %s",
        len(user_events),
        thread_event.id,
    )
    return user_events


def _delete_user_event_assigns(thread, assignees_data, *, context):
    """Remove UserEvent ASSIGN rows matching ``assignees_data`` on ``thread``.

    The ThreadEvent UNASSIGN row is the historical trace; the per-user
    UserEvent ASSIGN is removed because it is the source of truth for
    "currently assigned to me".
    """
    if not assignees_data:
        return 0

    user_ids = set()
    for assignee in assignees_data:
        raw_id = assignee.get("id")
        if not raw_id:
            continue
        try:
            user_ids.add(uuid.UUID(raw_id))
        except (ValueError, AttributeError):
            logger.warning(
                "Skipping unassign with invalid UUID '%s' in context %s",
                raw_id,
                context,
            )

    if not user_ids:
        return 0

    deleted, _ = models.UserEvent.objects.filter(
        thread=thread,
        user_id__in=user_ids,
        type=enums.UserEventTypeChoices.ASSIGN,
    ).delete()

    if deleted:
        logger.info(
            "Deleted %d UserEvent ASSIGN(s) in context %s",
            deleted,
            context,
        )

    return deleted


def _sync_user_event_mentions(thread_event, thread, mentions_data):
    """Reconcile UserEvent MENTION rows against the current mentions payload.

    - Creates rows for newly mentioned users.
    - Deletes rows for users no longer mentioned (so stale entries do not
      linger in the "Mentioned" folder after an edit).
    - Leaves matching rows untouched, preserving ``read_at`` across edits.

    Invalid or unauthorised mentions are silently dropped with a warning.
    """
    new_valid_user_ids = _validate_user_ids_with_access(
        thread, mentions_data, context=str(thread_event.id)
    )

    existing_user_ids = set(
        models.UserEvent.objects.filter(
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
        ).values_list("user_id", flat=True)
    )

    to_add = new_valid_user_ids - existing_user_ids
    to_remove = existing_user_ids - new_valid_user_ids

    if to_remove:
        deleted_count, _ = models.UserEvent.objects.filter(
            thread_event=thread_event,
            type=enums.UserEventTypeChoices.MENTION,
            user_id__in=to_remove,
        ).delete()
        if deleted_count:
            logger.info(
                "Deleted %d UserEvent MENTION(s) for ThreadEvent %s",
                deleted_count,
                thread_event.id,
            )

    if to_add:
        user_events = [
            models.UserEvent(
                user_id=user_id,
                thread=thread,
                thread_event=thread_event,
                type=enums.UserEventTypeChoices.MENTION,
            )
            for user_id in to_add
        ]
        # ignore_conflicts=True absorbs the (user, thread_event, type) unique
        # constraint when concurrent edits race on the same ThreadEvent.
        models.UserEvent.objects.bulk_create(user_events, ignore_conflicts=True)
        logger.info(
            "Created %d UserEvent MENTION(s) for ThreadEvent %s",
            len(user_events),
            thread_event.id,
        )


def _absorb_unassign_in_undo_window(*, thread, author, assignee_ids, assignees_data):
    """Absorb an UNASSIGN against recent ASSIGN events by the same author.

    When an UNASSIGN arrives within ``UNDO_WINDOW_SECONDS`` after an ASSIGN
    by the same author for the same user, treat it as an undo: the user is
    stripped from the original ASSIGN event (deleted if it becomes empty)
    and the matching ``UserEvent ASSIGN`` row is removed. No UNASSIGN
    ThreadEvent is emitted for these users.

    Returns the set of absorbed user UUIDs. Locks recent ASSIGN rows with
    ``select_for_update`` so concurrent requests cannot double-undo.
    """
    if not assignee_ids:
        return set()

    cutoff = timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS)
    target_ids = set(assignee_ids)
    recent_assigns = list(
        models.ThreadEvent.objects.select_for_update()
        .filter(
            thread=thread,
            author=author,
            type=enums.ThreadEventTypeChoices.ASSIGN,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
    )

    absorbed = set()
    for event in recent_assigns:
        original_assignees = (event.data or {}).get("assignees", [])
        if not original_assignees:
            continue
        remaining = []
        changed = False
        for assignee in original_assignees:
            try:
                aid = uuid.UUID(assignee["id"])
            except (ValueError, KeyError, TypeError):
                remaining.append(assignee)
                continue
            if aid in target_ids and aid not in absorbed:
                absorbed.add(aid)
                changed = True
            else:
                remaining.append(assignee)
        if not changed:
            continue
        if remaining:
            event.data = {**event.data, "assignees": remaining}
            event.save()
        else:
            event.delete()

    if absorbed:
        absorbed_data = [a for a in assignees_data if uuid.UUID(a["id"]) in absorbed]
        _delete_user_event_assigns(thread, absorbed_data, context="<undo-window>")

    return absorbed


# ---------------------------------------------------------------------------
# Public service API — ASSIGN / UNASSIGN
# ---------------------------------------------------------------------------


@transaction.atomic
def assign_users(*, thread, author, assignees_data):
    """Assign users to ``thread`` by creating ASSIGN events.

    - Idempotent: users already holding a ``UserEvent ASSIGN`` on the
      thread are filtered out. If every requested assignee is already
      assigned, returns ``None`` (the caller should respond 204).
    - Validates that every new assignee has full edit rights — if any
      lacks them, raises ``ValueError``. The viewset translates this to
      a 400; admin paths surface it as a Django form error.
    - Persists a single ThreadEvent ASSIGN containing the new assignees
      and creates the matching ``UserEvent`` rows in the same atomic
      transaction.

    Returns the persisted ThreadEvent, or ``None`` when nothing was new.
    """
    if not assignees_data:
        return None

    assignee_ids = [uuid.UUID(a["id"]) for a in assignees_data]
    already_assigned = set(
        models.UserEvent.objects.filter(
            thread=thread,
            user_id__in=assignee_ids,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).values_list("user_id", flat=True)
    )
    new_assignees = [
        a for a in assignees_data if uuid.UUID(a["id"]) not in already_assigned
    ]
    if not new_assignees:
        return None

    new_assignee_ids = {uuid.UUID(a["id"]) for a in new_assignees}
    editable_user_ids = set(
        models.ThreadAccess.objects.editor_user_ids(
            thread.id, user_ids=new_assignee_ids
        )
    )
    if editable_user_ids != new_assignee_ids:
        raise ValueError("Assignee must have editor access on the thread")

    thread_event = models.ThreadEvent.objects.create(
        thread=thread,
        author=author,
        type=enums.ThreadEventTypeChoices.ASSIGN,
        data={"assignees": new_assignees},
    )
    _create_user_event_assigns(thread_event, thread, new_assignees)
    return thread_event


@transaction.atomic
def unassign_users(*, thread, author, assignees_data):
    """Unassign users from ``thread`` by creating an UNASSIGN event.

    - Filters the payload down to users currently holding a
      ``UserEvent ASSIGN`` on the thread; everything else is a no-op.
    - Within ``UNDO_WINDOW_SECONDS`` of a recent ASSIGN by the same
      author, falls back to the "undo" flow: the original ASSIGN event
      is amended (or deleted if empty), no UNASSIGN ThreadEvent is
      emitted, and the matching ``UserEvent ASSIGN`` rows are removed.
    - Otherwise persists a single ThreadEvent UNASSIGN and deletes the
      matching ``UserEvent`` rows in the same atomic transaction.

    Returns the persisted ThreadEvent, or ``None`` when the request was
    fully absorbed by undo or matched no active assignment.
    """
    if not assignees_data:
        return None

    assignee_ids = [uuid.UUID(a["id"]) for a in assignees_data]
    active_assignee_ids = set(
        models.UserEvent.objects.filter(
            thread=thread,
            user_id__in=assignee_ids,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).values_list("user_id", flat=True)
    )
    if not active_assignee_ids:
        return None

    assignees_data = [
        a for a in assignees_data if uuid.UUID(a["id"]) in active_assignee_ids
    ]
    assignee_ids = [uuid.UUID(a["id"]) for a in assignees_data]

    absorbed = _absorb_unassign_in_undo_window(
        thread=thread,
        author=author,
        assignee_ids=assignee_ids,
        assignees_data=assignees_data,
    )
    if absorbed:
        assignees_data = [
            a for a in assignees_data if uuid.UUID(a["id"]) not in absorbed
        ]
        if not assignees_data:
            return None

    thread_event = models.ThreadEvent.objects.create(
        thread=thread,
        author=author,
        type=enums.ThreadEventTypeChoices.UNASSIGN,
        data={"assignees": assignees_data},
    )
    _delete_user_event_assigns(thread, assignees_data, context=str(thread_event.id))
    return thread_event


# ---------------------------------------------------------------------------
# Public service API — IM (mentions)
# ---------------------------------------------------------------------------


@transaction.atomic
def sync_im_mentions(*, thread_event):
    """Reconcile ``UserEvent MENTION`` rows for an IM ThreadEvent.

    Called by the viewset both on create (after a new IM is persisted)
    and on update (after an edit changes the mentions list). Idempotent:
    a re-sync with unchanged mentions does nothing.
    """
    if thread_event.type != enums.ThreadEventTypeChoices.IM:
        return
    mentions_data = (thread_event.data or {}).get("mentions", []) or []
    _sync_user_event_mentions(thread_event, thread_event.thread, mentions_data)


# ---------------------------------------------------------------------------
# Public service API — access cleanup
# ---------------------------------------------------------------------------


def _cleanup_invalid_assignments(thread, user_ids):
    """Unassign users that lost full edit rights on ``thread``.

    Among ``user_ids``, keeps only those currently assigned *and* no
    longer qualifying as editors, then records a single system
    ``ThreadEvent(type=UNASSIGN, author=None)`` grouping all of them and
    removes the matching ``UserEvent ASSIGN`` rows. A user reachable
    through multiple mailboxes keeps the assignment as long as one path
    still grants editor rights.
    """
    user_ids = set(user_ids)
    if not user_ids:
        return

    assigned_user_ids = set(
        models.UserEvent.objects.filter(
            thread=thread,
            user_id__in=user_ids,
            type=enums.UserEventTypeChoices.ASSIGN,
        ).values_list("user_id", flat=True)
    )
    if not assigned_user_ids:
        return

    still_editors = set(
        models.ThreadAccess.objects.editor_user_ids(
            thread.id, user_ids=assigned_user_ids
        )
    )
    to_unassign = assigned_user_ids - still_editors
    if not to_unassign:
        return

    users = models.User.objects.filter(id__in=to_unassign).values(
        "id", "full_name", "email"
    )
    assignees_data = [
        {"id": str(u["id"]), "name": u["full_name"] or u["email"] or ""} for u in users
    ]
    if not assignees_data:
        return

    thread_event = models.ThreadEvent.objects.create(
        thread=thread,
        type=enums.ThreadEventTypeChoices.UNASSIGN,
        author=None,
        data={"assignees": assignees_data},
    )
    _delete_user_event_assigns(thread, assignees_data, context=str(thread_event.id))
    logger.info(
        "Auto-unassigned %d user(s) on thread %s after access change",
        len(assignees_data),
        thread.id,
    )


def _cleanup_invalid_mentions(thread, user_ids):
    """Remove ``UserEvent MENTION`` rows for users that lost access to
    ``thread``.

    Unlike ``_cleanup_invalid_assignments``, no system ``ThreadEvent`` is
    recorded: the IM events containing the mention payload stay
    untouched as historical record; only the per-user notification rows
    are dropped. A user reachable through multiple mailboxes keeps their
    mentions as long as one path still grants any access.
    """
    user_ids = set(user_ids)
    if not user_ids:
        return

    mentioned_user_ids = set(
        models.UserEvent.objects.filter(
            thread=thread,
            user_id__in=user_ids,
            type=enums.UserEventTypeChoices.MENTION,
        )
        .values_list("user_id", flat=True)
        .distinct()
    )
    if not mentioned_user_ids:
        return

    still_with_access = set(
        models.ThreadAccess.objects.viewer_user_ids(
            thread.id, user_ids=mentioned_user_ids
        )
    )
    to_clean = mentioned_user_ids - still_with_access
    if not to_clean:
        return

    deleted, _ = models.UserEvent.objects.filter(
        thread=thread,
        user_id__in=to_clean,
        type=enums.UserEventTypeChoices.MENTION,
    ).delete()
    if deleted:
        logger.info(
            "Auto-cleaned %d UserEvent MENTION(s) on thread %s after access change",
            deleted,
            thread.id,
        )


def _affected_user_ids_for_mailbox(mailbox):
    """Users that reach a thread through ``mailbox`` via MailboxAccess."""
    return list(mailbox.accesses.values_list("user_id", flat=True))


@transaction.atomic
def revoke_thread_access(*, thread_access):
    """Cleanup assignments and mentions after a ThreadAccess deletion.

    Must be called *after* the row has been deleted (in the same atomic
    transaction): the ``editor_user_ids`` / ``viewer_user_ids`` queries
    that decide who lost their rights need the deleted row to be gone.
    The in-memory instance still carries ``mailbox`` and ``thread`` so
    we can enumerate impacted users.
    """
    user_ids = _affected_user_ids_for_mailbox(thread_access.mailbox)
    _cleanup_invalid_assignments(thread_access.thread, user_ids)
    _cleanup_invalid_mentions(thread_access.thread, user_ids)


@transaction.atomic
def downgrade_thread_access(*, thread_access):
    """Cleanup assignments after a ThreadAccess loses EDITOR role.

    The caller must invoke this only after the role has actually moved
    away from EDITOR. Mentions are not cleaned up here: a downgrade
    EDITOR → VIEWER still grants read access, so mentions remain valid.
    """
    user_ids = _affected_user_ids_for_mailbox(thread_access.mailbox)
    _cleanup_invalid_assignments(thread_access.thread, user_ids)


def _threads_with_user_event(*, mailbox_id, user_id, event_type):
    """Threads where ``user_id`` holds a UserEvent of ``event_type`` and
    reaches the thread through ``mailbox_id``.

    Narrows to the relevant subset to avoid iterating over every thread
    shared with the mailbox — a user is typically assigned/mentioned on a
    small fraction of them.
    """
    return models.Thread.objects.filter(
        accesses__mailbox_id=mailbox_id,
        user_events__user_id=user_id,
        user_events__type=event_type,
    ).distinct()


@transaction.atomic
def revoke_mailbox_access(*, mailbox_access):
    """Cleanup assignments and mentions after a MailboxAccess deletion.

    Must be called *after* the row has been deleted (in the same atomic
    transaction). The in-memory instance still carries ``user_id`` and
    ``mailbox_id`` so we can scope the cleanup; the deleted row is
    excluded from ``editor_user_ids`` / ``viewer_user_ids`` because it
    no longer exists in the database.
    """
    mailbox_id = mailbox_access.mailbox_id
    user_id = mailbox_access.user_id

    assigned_threads = _threads_with_user_event(
        mailbox_id=mailbox_id,
        user_id=user_id,
        event_type=enums.UserEventTypeChoices.ASSIGN,
    )
    for thread in assigned_threads:
        _cleanup_invalid_assignments(thread, [user_id])

    mentioned_threads = _threads_with_user_event(
        mailbox_id=mailbox_id,
        user_id=user_id,
        event_type=enums.UserEventTypeChoices.MENTION,
    )
    for thread in mentioned_threads:
        _cleanup_invalid_mentions(thread, [user_id])


@transaction.atomic
def downgrade_mailbox_access(*, mailbox_access):
    """Cleanup assignments after a MailboxAccess role left
    ``MAILBOX_ROLES_CAN_EDIT``.

    The caller must invoke this only after the role has actually changed
    out of editing roles. Mentions stay untouched: every MailboxAccess
    role grants read access, so a downgrade alone never invalidates a
    mention.
    """
    mailbox_id = mailbox_access.mailbox_id
    user_id = mailbox_access.user_id

    assigned_threads = _threads_with_user_event(
        mailbox_id=mailbox_id,
        user_id=user_id,
        event_type=enums.UserEventTypeChoices.ASSIGN,
    )
    for thread in assigned_threads:
        _cleanup_invalid_assignments(thread, [user_id])

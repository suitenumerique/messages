
# Permissions & Data Model

## Core Data Model

### Entity Relationships

```text
User
├── MailboxAccess (role: VIEWER|EDITOR|SENDER|ADMIN)
│   └── Mailbox
│       ├── ThreadAccess (role: VIEWER|EDITOR)
│       │   └── Thread
│       │       ├── Message
│       │       │   ├── sender → Contact
│       │       │   ├── recipients → MessageRecipient → Contact
│       │       │   ├── parent → Message (reply chain)
│       │       │   ├── blob → Blob (raw MIME)
│       │       │   ├── draft_blob → Blob (JSON draft content)
│       │       │   └── attachments → Attachment → Blob (only for drafts)
│       │       ├── events → ThreadEvent (im / assign / unassign)
│       │       │   └── user_events → UserEvent (mention / assign)
│       │       ├── accesses → ThreadAccess (multiple mailboxes)
│       │       └── labels → Label (M2M)
│       ├── contacts → Contact
│       ├── labels → Label
│       └── blobs → Blob
├── user_events → UserEvent (per-user notifications, thread-scoped)
└── MailDomainAccess (role: ADMIN)
    └── MailDomain
        └── mailboxes → Mailbox (via domain FK)
```

### Key Models

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **User** | Identity (OIDC) | `sub`, `email`, `full_name` |
| **Mailbox** | Email account | `local_part`, `domain` (FK) |
| **MailboxAccess** | User→Mailbox permission | `user`, `mailbox`, `role` (unique together) |
| **Thread** | Message thread | `subject`, denormalized flags (`has_trashed`, `is_spam`, etc.) |
| **ThreadAccess** | Mailbox→Thread permission | `thread`, `mailbox`, `role` (unique together) |
| **Message** | Email message | `thread`, `sender`, `parent`, flags (`is_draft`, `is_trashed`, etc.) |
| **ThreadEvent** | Timeline entry on a thread (comment, assign, unassign) | `thread`, `type`, `author`, `data` (JSON, schema-validated per type) |
| **UserEvent** | Per-user notification derived from a ThreadEvent | `user`, `thread`, `thread_event`, `type`, `read_at` |
| **Contact** | Email address entity | `email`, `mailbox`, `name` |
| **Label** | Folder/tag (hierarchical) | `name`, `slug`, `mailbox`, `threads` (M2M) |

## Role Hierarchies

### MailboxRoleChoices (User access to Mailbox)

```python
VIEWER = 1   # Read-only: view mailbox threads/messages
EDITOR = 2   # Edit: create drafts, flag, delete, manage thread access
SENDER = 3   # Send: EDITOR + can send messages
ADMIN  = 4   # Admin: SENDER + manage mailbox accesses, labels, templates, import
```

Role groups defined in `enums.py`:
- `MAILBOX_ROLES_CAN_EDIT = [EDITOR, SENDER, ADMIN]`
- `MAILBOX_ROLES_CAN_SEND = [SENDER, ADMIN]`

### ThreadAccessRoleChoices (Mailbox access to Thread)

```python
VIEWER = 1   # Read-only: view thread messages and events
EDITOR = 2   # Edit: create replies, flag messages, manage thread sharing, assign
```

Role group:
- `THREAD_ROLES_CAN_EDIT = [EDITOR]`

### Event Types

```python
# ThreadEvent.type (stored on the thread timeline)
IM       = "im"        # Internal comment, may embed mentions in data
ASSIGN   = "assign"    # User(s) newly assigned to the thread
UNASSIGN = "unassign"  # User(s) removed from the thread

# UserEvent.type (per-user notification, derived from ThreadEvent)
MENTION  = "mention"   # One per (user, message mention); read_at tracks ack
ASSIGN   = "assign"    # At most one per (user, thread); source of truth for "assigned"
```

`UserEvent` is **not** mailbox-scoped: a user reachable through several mailboxes sees the same notification everywhere.

## Permission Classes

Defined in `core/api/permissions.py`. The table below lists the main ones and the rule they enforce.

| Class | Rule |
|-------|------|
| `IsAuthenticated` | Baseline — user is logged in. |
| `IsAllowedToAccess` | Read access to a Mailbox/Thread/Message/ThreadEvent via any `MailboxAccess` → `ThreadAccess` path. |
| `HasThreadEditAccess` | Full edit rights: `ThreadAccess.role == EDITOR` **AND** `MailboxAccess.role ∈ MAILBOX_ROLES_CAN_EDIT` on the same mailbox. |
| `HasThreadCommentAccess` | Allowed to author internal comments: any `ThreadAccess` (viewer or editor) on a mailbox where the user has `MAILBOX_ROLES_CAN_EDIT`. |
| `HasThreadEventWriteAccess` | Type-aware: `im` events follow the comment rule; every other `ThreadEvent` type requires full edit rights. Update/destroy is author-only. |
| `IsAllowedToCreateMessage` | User must have `MAILBOX_ROLES_CAN_EDIT` on the sender mailbox (plus EDITOR `ThreadAccess` when replying). |
| `IsAllowedToManageThreadAccess` | Managing a `ThreadAccess` requires full edit rights on the thread. |
| `IsMailboxAdmin` / `IsMailDomainAdmin` | Admin paths for mailbox and maildomain management. |
| `HasChannelScope` | Scope check for Channel-authenticated calls; `CHANNEL_API_KEY_SCOPES_GLOBAL_ONLY` further requires `scope_level=global`. |

Shared ORM helpers (`core/models.py`):
- `ThreadAccess.objects.editable_by(user, mailbox_id=None)` — rows matching the full-edit-rights rule.
- `ThreadAccess.objects.editor_user_ids(thread_id, user_ids=None)` — user ids with full edit rights on a thread.

## Key Design Principles

1. **Two-level permission model.** User→Mailbox (`MailboxAccess`) and Mailbox→Thread (`ThreadAccess`) are independent and composed for every access check. Edit-level actions always verify **both** sides.
2. **ThreadAccess is per-mailbox.** Each mailbox has its own role on a given thread, enabling selective sharing and per-mailbox `read_at` / `starred_at` state.
3. **Flags are shared state.** Message flags (`is_trashed`, `is_spam`, `is_unread`, etc.) live on the Message and mutate the thread for everyone — they require EDITOR `ThreadAccess`.
4. **Thread stats are denormalized.** Thread has boolean fields (`has_trashed`, `is_spam`, …) updated by `thread.update_stats()` after message flag changes. Mention/assignment stats (`has_mention`, `has_unread_mention`, `has_assigned_to_me`, `has_unassigned`) are **not** stored: they are computed per request via `Exists(UserEvent...)` annotations in `ThreadViewSet`.
5. **Comments relax the thread role.** Posting or editing an `im` `ThreadEvent` only requires VIEWER `ThreadAccess` + mailbox edit rights. Assign/unassign and any other event type keep the stricter full-edit-rights policy.
6. **Event mutations are author-only.** Update and destroy of a `ThreadEvent` are refused for non-authors, regardless of role. A configurable window (`settings.MAX_THREAD_EVENT_EDIT_DELAY`) can close the edit/delete path entirely after creation.
7. **Assignment is derived from the event log.** `UserEvent(type=ASSIGN)` is the source of truth for "who is assigned"; there is no denormalized field on Thread. A partial `UniqueConstraint` enforces at most one active ASSIGN per `(user, thread)` and absorbs races between concurrent ASSIGN requests.
8. **Undo window for assignments.** An UNASSIGN within `UNDO_WINDOW_SECONDS` (120s) of the matching ASSIGN, by the same author, is absorbed: the original ASSIGN `ThreadEvent` is trimmed or deleted, the `UserEvent ASSIGN` is removed, and no UNASSIGN event is emitted.
9. **Access changes cascade to assignments.** Downgrading or removing a `ThreadAccess` / `MailboxAccess` triggers `cleanup_invalid_assignments`, which emits a single system `ThreadEvent(type=UNASSIGN, author=None)` for any assignee who lost full edit rights (re-evaluated across all their mailboxes).
10. **Mentions survive edits idempotently.** Editing an `im` event diffs the mentions payload and reconciles `UserEvent(MENTION)` rows; unchanged mentions keep their `read_at`, removed ones disappear from the user's "Mentioned" view, new ones are created.

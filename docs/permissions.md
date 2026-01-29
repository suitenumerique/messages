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
│       │       ├── accesses → ThreadAccess (multiple mailboxes)
│       │       └── labels → Label (M2M)
│       ├── contacts → Contact
│       ├── labels → Label
│       └── blobs → Blob
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
VIEWER = 1   # Read-only: view thread messages
EDITOR = 2   # Edit: create replies, flag messages, manage thread sharing
```

Role group:
- `THREAD_ROLES_CAN_EDIT = [EDITOR]`

### Key Design Principles

1. **Two-level permission model**: User→Mailbox (MailboxAccess) and Mailbox→Thread (ThreadAccess) are independent.
2. **ThreadAccess is per-mailbox**: Each mailbox has its own access level to a thread, enabling selective sharing.
3. **Flags are shared state**: Message flags (`is_trashed`, `is_spam`, `is_unread`, etc.) are stored on the Message model directly, not per-user. Modifying them requires EDITOR ThreadAccess.
4. **Thread stats are denormalized**: Thread has boolean fields (`has_trashed`, `is_spam`, etc.) updated by `thread.update_stats()` after message flag changes.

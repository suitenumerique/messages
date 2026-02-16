# PST Import

This document describes how PST (Personal Storage Table) files are imported
into the application.

## Overview

PST files are the native file format for Microsoft Outlook mailboxes. The
import pipeline reads messages from a PST file stored in S3, converts them to
RFC 5322 (EML) format, and delivers them through the standard inbound message
pipeline with `is_import=True`.

**Entry point:** `process_pst_file_task` in `core/services/importer/pst_tasks.py`

## Architecture

```
S3 (message-imports bucket)
  │
  ▼
S3SeekableReader (block-aligned LRU cache)
  │
  ▼
pypff (libpff) ── reads PST B-tree structures
  │
  ▼
pst.py ── folder detection, message extraction, EML reconstruction
  │
  ▼
pst_tasks.py ── Celery task, flag/label mapping, progress reporting
  │
  ▼
deliver_inbound_message(is_import=True)
```

## S3 Seekable Reader

PST files are read directly from S3 without downloading to disk. The
`S3SeekableReader` class (`core/services/importer/s3_seekable.py`) implements
a seekable file-like object backed by S3 range requests.

For PST files, the reader uses `BUFFER_NONE` strategy with a **block-aligned
LRU cache** — 64 KB blocks with up to 2048 cache slots (128 MB max). This
matches pypff's random-access pattern when traversing PST B-tree structures.

## Folder Detection

PST files contain a hierarchy of folders. Some are "special" folders (Inbox,
Sent Items, Drafts, etc.) that need specific handling during import. Detection
uses a **3-tier fallback strategy**:

### Tier 1: Message Store Entry IDs

The PST message store (`pst.get_message_store()`) may contain entry ID
properties that directly identify special folders:

| Property                      | Tag      | Folder         |
|-------------------------------|----------|----------------|
| `PR_IPM_SUBTREE_ENTRYID`     | `0x35E0` | IPM Subtree    |
| `PR_IPM_OUTBOX_ENTRYID`      | `0x35E2` | Outbox         |
| `PR_IPM_WASTEBASKET_ENTRYID` | `0x35E3` | Deleted Items  |
| `PR_IPM_SENTMAIL_ENTRYID`    | `0x35E4` | Sent Items     |

Each entry ID is 24 bytes. The last 4 bytes (LE uint32) contain the folder
identifier, which matches `folder.get_identifier()` from pypff.

`PR_VALID_FOLDER_MASK` (`0x35DF`) indicates which entry IDs are valid:

| Bit | Folder              |
|-----|---------------------|
| 0   | IPM Subtree         |
| 1   | Inbox               |
| 2   | Outbox              |
| 3   | Deleted Items       |
| 4   | Sent Items          |
| 7   | Finder / Search     |

In locally-created Outlook PSTs, all bits are typically set (`0xFF`) and Tier
1 detects all special folders. In Exchange/O365-exported PSTs, only some bits
may be set — for example `0x89` (subtree + wastebasket + finder only), meaning
Sent Items, Outbox, and Inbox entry IDs are absent and **Tier 1 only detects
Deleted Items**. The remaining folders must be detected by Tier 2 or 3.

| PST type                            | `PR_VALID_FOLDER_MASK` | Tier 1 detects         | Needs fallback for               |
|-------------------------------------|------------------------|------------------------|----------------------------------|
| Local Outlook                       | `0xFF`                 | All special folders    | Nothing                          |
| Exchange/O365 migration             | `0x89`                 | Deleted Items only     | Inbox, Sent, Outbox, Drafts      |
| Other/unknown                       | varies                 | Whatever IDs are valid | Anything missing                 |

### Tier 2: SourceWellKnownFolderType (Named Property)

PSTs exported by Microsoft's migration tools (Exchange/O365) contain a named
property that identifies special folders. This covers exactly the gap left by
Tier 1 on Exchange PSTs — Inbox, Sent Items, Outbox, and Drafts:

- **GUID:** `{9137a2fd-2fa5-4409-91aa-2c3ee697350a}`
- **Name:** `SourceWellKnownFolderType`

This is a named property, so its tag varies between PST files (resolved via
the Name-to-ID Map at NID `0x61`). The values are:

| Value | Folder Type    |
|-------|----------------|
| 10    | Inbox          |
| 11    | Sent Items     |
| 12    | Outbox         |
| 14    | Deleted Items  |
| 17    | Drafts         |

The resolution process:
1. Read the Entry Stream, GUID Stream, and String Stream from the
   Name-to-ID Map
2. Parse NAMEID records (8 bytes each) to find the one matching the target
   GUID + string name
3. Calculate the NPID: `0x8000 + wPropIdx`
4. Read that property tag from each folder

This tier is only available on Exchange/O365 migration PSTs. It is absent from
locally-created Outlook PSTs (which have all entry IDs via Tier 1 anyway).

### Tier 3: Folder Name Matching

As a final fallback, folder names are matched against a dictionary of known
Outlook default folder names in multiple languages (English, French, German,
Spanish, Italian, Dutch, Portuguese, Russian, Polish, Czech, Hungarian, Danish,
Norwegian, Swedish, Finnish, Turkish, Japanese, Chinese, Korean, Arabic,
Hebrew, Ukrainian, Romanian).

This matching is only applied to **direct children of the IPM subtree** to
avoid false positives on user-created subfolders with coincidental names.

### Detection Priority

For each folder, detection is attempted in this order:
1. Entry ID match (Tier 1) — highest priority
2. SourceWellKnownFolderType match (Tier 2)
3. Folder name match (Tier 3) — only for IPM subtree direct children
4. Normal folder (no special handling)

## IPM Subtree

The "Top of Personal Folders" wrapper folder is skipped by locating the IPM
subtree via `PR_IPM_SUBTREE_ENTRYID` on the message store. All folder
iteration starts from the IPM subtree, not the root folder. This excludes
internal folders like "Freebusy Data", "Search Root", etc.

## Message Processing

### EML Reconstruction

Each pypff message is converted to RFC 5322 format by `reconstruct_eml()`:

1. **Transport headers** (if available): Used for threading headers
   (`Message-ID`, `In-Reply-To`, `References`), sender, recipients, date
2. **MAPI properties** (fallback): Sender from `PR_SENDER_*` properties,
   recipients from the recipient table, date from `delivery_time` /
   `client_submit_time`
3. **Body**: Plain text and/or HTML body
4. **Attachments**: Filenames from `PR_ATTACH_LONG_FILENAME` / `PR_ATTACH_FILENAME`
   MAPI properties, with MIME type from `PR_ATTACH_MIME_TAG`

### Sender Resolution

For Exchange (EX) address types, SMTP addresses are resolved in order:
1. `PR_SENDER_SMTP_ADDRESS`
2. `PR_SENDER_EMAIL_ADDRESS` (if it contains `@`)
3. `sender_name` parsed as email
4. Store owner email from message store `PR_DISPLAY_NAME` (fallback for
   Exchange sent items with no SMTP address)

### Flag and Label Mapping

| Folder Type  | IMAP Label | IMAP Flags  | `is_import_sender` |
|-------------|------------|-------------|-------------------|
| Inbox       | *(none)*   |             | `False`           |
| Sent Items  | `Sent`     |             | `True`            |
| Drafts      | *(none)*   | `\Draft`    | `False`           |
| Deleted     | `Trash`    |             | `False`           |
| Outbox      | `OUTBOX`   |             | `True`            |
| Normal      | folder path|             | `False`           |

**Subfolders of special folders** inherit the parent's special type (so they
keep `is_import_sender`, IMAP flags, etc.) and get their own subfolder name as
an additional label. For example, a "Sent Items/Archives 2024" subfolder yields
`is_import_sender=True` + labels `Sent` and `Archives 2024`. Deeper nesting
builds hierarchical paths: "Sent Items/Projects/Work" yields labels `Sent` and
`Projects/Work`.

Per-message flags from MAPI properties:
- `MSGFLAG_READ` → `\Seen`
- `MSGFLAG_UNSENT` or Drafts folder → `\Draft`
- `FLAG_STATUS >= 2` (follow-up) → `\Flagged`

### Chronological Ordering

Messages are collected in a first pass (lightweight metadata only), sorted by
`delivery_time` (oldest first, `None` timestamps last), then reconstructed to
EML one at a time in the second pass. This ensures proper threading while
limiting memory usage.

## Key Files

| File | Description |
|------|-------------|
| `core/services/importer/pst.py` | PST parsing, folder detection, EML reconstruction |
| `core/services/importer/pst_tasks.py` | Celery task, flag/label logic, progress reporting |
| `core/services/importer/s3_seekable.py` | S3-backed seekable file reader with LRU cache |
| `core/tests/importer/test_pst_import.py` | Unit and integration tests |

## Dependencies

- **pypff** (libpff-python): Python bindings for reading PST files. Requires
  `build-essential` for compilation in Docker.

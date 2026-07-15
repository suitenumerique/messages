# MBOX Import/Export Format

This document describes how Messages handles MBOX files for importing and exporting emails, including how labels, flags, and metadata are preserved.

## Overview

MBOX is a standard format for storing email messages in a single file. Messages uses the mboxrd variant, which is compatible with most email clients and tools including:

- Google Takeout
- Thunderbird (via ImportExportTools NG)
- Dovecot
- OfflineIMAP
- Apple Mail
- mu4e / notmuch

## Export Format

When exporting a mailbox, Messages creates a gzip-compressed MBOX file (`.mbox.gz`) containing all messages with their metadata preserved in standard headers.

### Message Flags

Message flags are exported using the standard mbox `Status` and `X-Status` headers:

| Header | Flag | Meaning |
|--------|------|---------|
| `Status: R` | R | Read (seen) |
| `Status: O` | O | Old (not recent) - always set for exports |
| `X-Status: A` | A | Answered (sent messages) |
| `X-Status: F` | F | Flagged (starred) |
| `X-Status: T` | T | Draft |

**Examples:**
```text
Status: RO          # Read message
Status: O           # Unread message
X-Status: F         # Starred message
X-Status: AF        # Sent and starred message
X-Status: T         # Draft message
```

### Labels

Labels are exported using the `X-Keywords` header, which is recognized by Dovecot, OfflineIMAP, mu4e, and other Unix mail tools.

**Format:** Comma-separated list with quoted strings for labels containing spaces or commas.

```text
X-Keywords: work, important, "project alpha"
```

### Complete Example

A read, starred message with labels would have these headers injected:

```text
Status: RO
X-Status: F
X-Keywords: work, important
```

## Import Format

When importing MBOX files, Messages recognizes and processes the following headers:

### Labels Headers

| Header | Source | Format |
|--------|--------|--------|
| `X-Gmail-Labels` | Google Takeout | Comma-separated, quoted strings |
| `X-Keywords` | Dovecot/OfflineIMAP/mu4e | Comma or space-separated |

Both headers are parsed and combined. The importer handles:
- Comma-separated values: `label1, label2, label3`
- Space-separated values: `label1 label2 label3` (Dovecot format)
- Quoted strings: `"label with spaces", simple-label`

### Special Label Handling

Certain labels are mapped to message flags instead of being stored as labels:

| Label Names | Maps To |
|-------------|---------|
| `Drafts`, `[Gmail]/Drafts`, `DRAFT` | `is_draft` flag |
| `Sent`, `[Gmail]/Sent Mail`, `OUTBOX` | `is_sender` flag |
| `Starred`, `[Gmail]/Starred` | `is_starred` flag |
| `Trash`, `[Gmail]/Corbeille` | `is_trashed` flag |
| `Spam`, `QUARANTAINE` | `is_spam` flag |
| `Archived` | `is_archived` flag |

Labels like `INBOX`, `Promotions`, `Social`, `[Gmail]/Important`, and `[Gmail]/All Mail` are ignored.

### IMAP Flags

When importing via IMAP, standard IMAP flags are also recognized:
- `\Seen` - marks message as read
- `\Draft` - marks message as draft
- `\Flagged` - marks message as starred

## Roundtrip Compatibility

Messages exported from this system can be re-imported with full fidelity:

1. **Labels** are preserved via `X-Keywords` header
2. **Read/unread status** is preserved via `Status: R` header
3. **Starred status** is preserved via `X-Status: F` header
4. **Draft status** is preserved via `X-Status: T` header
5. **Sent status** is preserved via `X-Status: A` header

## Compatibility Notes

### Google Takeout

Google Takeout exports use the `X-Gmail-Labels` header. When importing Google Takeout files:
- All Gmail labels are recognized and imported
- System labels like `[Gmail]/Drafts` are mapped to flags
- Custom labels are preserved as-is

### Thunderbird

Thunderbird's native tags use IMAP keywords (`$Label1`, `$Label2`, etc.) which are not directly compatible. However:
- Thunderbird can import our MBOX files
- Use ImportExportTools NG for best results
- Labels will appear in the `X-Keywords` header but may not display as Thunderbird tags

### Dovecot

Dovecot uses `X-Keywords` with space-separated values. Our importer handles both formats:
- Space-separated: `X-Keywords: work important urgent`
- Comma-separated: `X-Keywords: work, important, urgent`

### Apple Mail

Apple Mail's export format (`.mbox` packages) can be imported. However, Apple Mail does not preserve labels in its exports, so label information may be lost when migrating from Apple Mail.

## Technical Details

### MBOX Variant

We use the mboxrd format where:
- Messages are separated by "From " lines at the start of a line
- "From " at the beginning of body lines is escaped as ">From "
- Each message ends with a blank line

### Header Injection

When exporting, metadata headers (`Status`, `X-Status`, `X-Keywords`) are injected at the end of the existing email headers, just before the blank line that separates headers from the body. This preserves the original email structure while adding metadata.

### Character Encoding

- Labels are UTF-8 encoded
- Labels containing commas, spaces, or quotes are enclosed in double quotes
- Labels containing double-quote characters are not currently supported for round-trip import

# MBOX Import/Export Format

This document describes how Messages handles MBOX files for importing and exporting emails, including how labels, flags, and metadata are preserved.

## Overview

MBOX is a standard format for storing email messages in a single file. Messages uses the mboxrd variant, the reversible one, which is compatible with most email clients and tools including:

- Google Takeout
- Thunderbird (via ImportExportTools NG)
- Dovecot
- OfflineIMAP
- Apple Mail
- mu4e / notmuch

## Export Format

When exporting a mailbox, Messages creates a gzip-compressed MBOX file (`.mbox.gz`) containing all messages with their metadata preserved in standard headers.

Drafts are not exported: a draft has no MIME representation yet (its body lives in a JSON blob). The notification email counts them on their own line, apart from messages that were skipped because something went wrong.

### Message Flags

Message flags are exported using the standard mbox `Status` and `X-Status` headers, which carry IMAP flags and nothing else:

| Header | Flag | Meaning |
|--------|------|---------|
| `Status: R` | R | Read (seen) |
| `Status: O` | O | Old (not recent) - always set for exports |
| `X-Status: F` | F | Flagged (starred) |
| `X-Status: T` | T | Draft |
| `X-Status: D` | D | Deleted (trashed) |

**Examples:**
```text
Status: RO          # Read message
Status: O           # Unread message
X-Status: F         # Starred message
X-Status: FD        # Starred, in the trash
```

`X-Status: A` (Answered) is never written: it means "this message was replied to", which we do not track. It is in particular *not* a marker for sent mail.

### Thunderbird Flags

`X-Mozilla-Status` (4 hex digits) and `X-Mozilla-Status2` (8 hex digits) carry read and starred state in the only form Thunderbird reads. It keeps flags in its `.msf` index rather than in the mbox, writing them into the file only when a folder is compacted, and it ignores `X-Keywords` and `X-Gmail-Labels` entirely, so a file without these headers imports as entirely unread.

Flag values come from Thunderbird's `nsMsgMessageFlags.idl`:

| Header | Bit | Meaning | Written when |
|--------|-----|---------|--------------|
| `X-Mozilla-Status` | `0x0001` | Read | the thread is read in this mailbox |
| `X-Mozilla-Status` | `0x0004` | Marked | the thread is starred in this mailbox |
| `X-Mozilla-Status2` | `0x10000000` | Attachment | `has_attachments` |

```text
X-Mozilla-Status: 0005
X-Mozilla-Status2: 00000000
```

Deliberately never written: `0x0008` Expunged, which means "deleted, pending folder compaction" and lets Thunderbird drop the message for good on the next compact. Trashed messages carry `X-Status: D` and the `Trash` label instead. `0x0002` Replied and `0x1000` Forwarded are states we do not track.

`X-Mozilla-Keys` (Thunderbird's tags) is stripped on export but never written: its keys are IMAP mod-UTF-7 transformations of tag names, and a key absent from the reader's profile does not display as a tag anyway.

On import, `X-Mozilla-Status` is read back for the same two bits, which is the only way a Thunderbird mbox carries that state. IMAP flags win over it when both are present: on an IMAP import the server is authoritative, and a message can carry an `X-Mozilla-Status` written by whoever sent it (Mozilla bug 196749).

### Labels

Two headers are written, with different audiences:

| Header | Contents | Read by |
|--------|----------|---------|
| `X-Keywords` | The user's own labels only | Dovecot, OfflineIMAP, mu4e and other Unix mail tools |
| `X-Gmail-Labels` | System labels, then the user's own labels | Google Takeout tooling, and our own importer |

**Format:** comma-separated list, with quoted strings for labels containing spaces or commas.

```text
X-Keywords: work, important, "project alpha"
X-Gmail-Labels: Inbox, Opened, work, important, "project alpha"
```

### System Labels

mbox has no way to say which folder a message came from: `Status`/`X-Status` only carry IMAP flags, and IMAP has no per-message "sent" flag (a sent folder is a *mailbox* attribute, `\Sent` in RFC 6154). Google Takeout works around this with pseudo-labels in `X-Gmail-Labels`, and we do the same:

| Label | Written when |
|-------|--------------|
| `Sent` | `is_sender` |
| `Trash` | `is_trashed` |
| `Spam` | `is_spam` |
| `Archived` | `is_archived` |
| `Inbox` | none of the above |
| `Starred` | the thread is starred in this mailbox |
| `Opened` / `Unread` | read state of the thread in this mailbox |

These are not IMAP keywords, which is why they stay out of `X-Keywords`: `Sent` there would show up as one of the user's own tags in Dovecot or mu4e.

### Complete Example

A read, starred, archived message with labels has these headers injected:

```text
Status: RO
X-Status: F
X-Keywords: work, important
X-Gmail-Labels: Archived, Starred, Opened, work, important
```

## Import Format

When importing MBOX files, Messages recognizes and processes the following headers:

### Labels Headers

| Header | Source | Format |
|--------|--------|--------|
| `X-Gmail-Labels` | Google Takeout | Comma-separated, quoted strings |
| `X-Keywords` | Dovecot/OfflineIMAP/mu4e | Comma or space-separated |

Both headers are parsed and combined (see `gmail_labels()` in `core/mda/utils.py`). The importer handles:
- Comma-separated values: `label1, label2, label3`
- Space-separated values: `label1 label2 label3` (Dovecot format)
- Quoted strings: `"label with spaces", simple-label`
- RFC 2047 encoded-words, which Takeout uses for non-ASCII label names

`Status` and `X-Status` are **not** read on mbox import. Read and starred state come from the labels below, or from `X-Mozilla-Status` for a Thunderbird file, which is why the exporter writes that state in three places.

### Special Label Handling

Certain labels are mapped to message state instead of being stored as labels (see `IMAP_LABEL_TO_MESSAGE_FLAG` in `core/services/importer/labels.py`):

| Label Names | Maps To |
|-------------|---------|
| `Drafts`, `[Gmail]/Drafts`, `DRAFT` | `is_draft` flag |
| `Sent`, `[Gmail]/Sent Mail`, `OUTBOX` | `is_sender` flag |
| `Trash`, `[Gmail]/Corbeille` | `is_trashed` flag (with `trashed_at`) |
| `Spam`, `QUARANTAINE` | `is_spam` flag |
| `Archived` | `is_archived` flag (with `archived_at`) |
| `Starred`, `[Gmail]/Starred` | `starred_at` on the thread's `ThreadAccess` |
| `Opened` / `Unread` | `read_at` on the thread's `ThreadAccess` |

Labels like `INBOX`, `Promotions`, `Social`, `[Gmail]/Important`, and `[Gmail]/All Mail` are ignored. Each table entry has French spellings too (`Messages envoyés`, `Corbeille`, `Ouvert`…), since those are what a French Takeout or IMAP account produces.

A label in the file that matches one of these names is consumed as state, so a user label literally named `Sent` or `Trash` will not survive an import as a label.

### IMAP Flags

When importing via IMAP, standard IMAP flags are also recognized:
- `\Seen` - marks message as read
- `\Draft` - marks message as draft
- `\Flagged` - marks message as starred

## Roundtrip Compatibility

Messages exported from this system can be re-imported with their state intact, all of it through the label headers:

| State | Carried by |
|-------|------------|
| Labels | `X-Keywords` and `X-Gmail-Labels` |
| Sent | `X-Gmail-Labels: Sent` |
| Trashed / spam / archived | `X-Gmail-Labels: Trash` / `Spam` / `Archived` |
| Read / unread | `X-Gmail-Labels: Opened` / `Unread`, and `X-Mozilla-Status` |
| Starred | `X-Gmail-Labels: Starred`, and `X-Mozilla-Status` |

Message bodies come back byte for byte, including lines starting with `From ` (see [MBOX variant](#mbox-variant)).

Covered end to end in `core/tests/exporter/test_export_task.py` by `test_export_reimport_roundtrip_preserves_flags` (state) and `test_export_reimport_roundtrip_preserves_body_bytes` (content), both exporting a mailbox and re-importing it into another one.

Known limitations:

- Drafts are not exported at all, so they cannot come back.
- A label containing a double-quote character does not survive the roundtrip.

## Compatibility Notes

### Google Takeout

Google Takeout exports use the `X-Gmail-Labels` header. When importing Google Takeout files:
- All Gmail labels are recognized and imported
- System labels like `[Gmail]/Drafts` are mapped to flags
- Custom labels are preserved as-is

### Thunderbird

Read and starred state survives, through `X-Mozilla-Status` (see [Thunderbird Flags](#thunderbird-flags)). Tags do not:
- Thunderbird can import our MBOX files
- Use ImportExportTools NG for best results
- Its native tags are IMAP keywords (`$label1`, `$label2`, …) stored in `X-Mozilla-Keys`, which we do not write, so our labels reach it in `X-Keywords` and do not display as tags
- Everything lands in a single folder: we export one flat file, where Thunderbird expects one mbox file per folder

### Dovecot

Dovecot uses `X-Keywords` with space-separated values. Our importer handles both formats:
- Space-separated: `X-Keywords: work important urgent`
- Comma-separated: `X-Keywords: work, important, urgent`

### Apple Mail

Apple Mail's export format (`.mbox` packages) can be imported. However, Apple Mail does not preserve labels in its exports, so label information may be lost when migrating from Apple Mail.

## Technical Details

### MBOX Variant

We use the mboxrd format, where:
- Messages are separated by "From " lines at the start of a line
- "From " at the beginning of a line is escaped as ">From ", and an already escaped `>From ` becomes `>>From `, and so on
- Each message ends with a blank line

Escaping the already-escaped forms is what makes mboxrd reversible: a reader strips exactly one `>` and gets the original bytes back. mboxo, which escapes only the bare `From `, cannot be reversed, because a line the author really did start with `>From ` is indistinguishable from an escaped one.

On import we strip one `>` from any `>+From ` line, and drop the blank line that separates two messages (the message's byte range runs up to the next `From ` line, so it ends with that blank line). Together with the export side, a message survives a roundtrip byte for byte, DKIM signature included.

The one place where the mboxo ambiguity still bites is a **third-party mboxo file**, Google Takeout's among them: a body line that genuinely started with `>From ` there loses its `>`. Not unescaping at all is worse, since then every line the writer escaped keeps a `>` it never had.

### Line Endings

RFC 4155 says an mbox database MUST use a bare LF and MUST NOT use CRLF. We do not convert: messages are written with the line endings they were stored with, which for anything that arrived over SMTP means CRLF.

This is deliberate. Converting to LF is one-way (the original bytes cannot be restored on the way back in) and it breaks the DKIM body hash, so byte fidelity wins over conformance here. It also costs nothing in practice: Google Takeout writes CRLF too, so anything that reads a Takeout archive reads ours. Our own test fixture `core/tests/resources/messages.mbox` is a real Takeout file and contains zero bare LFs.

### Separator Detection

A `From ` line is only treated as a message separator when both hold:

1. It sits at the start of the file, or directly after an empty line
2. It either reads like a postmark (`From <sender> <weekday> ...`) or is followed by a header line

Without rule 2, a body paragraph beginning "From my point of view..." after a blank line splits the message in two, and the fragment is delivered as its own message with no error anywhere. That is the oldest bug in the format (Mozilla bug 355237); the rule is the one Mail::Box uses. `From ` lines rejected by either rule are counted and logged once per file, since a writer that failed to escape them usually failed for all of them.

### Header Injection

When exporting, the metadata headers (`Status`, `X-Status`, `X-Keywords`, `X-Gmail-Labels`) are prepended at the top of the message, before the `Received:` chain, so the chain keeps its chronological descending order.

Any copy of those four headers already present in the stored message is stripped first. A message imported from Takeout or an IMAP server keeps the headers it arrived with, the mailbox state has moved on since, and the importer reads *every* occurrence of the label headers: without stripping, a re-import would resurrect stale labels and flags.

### Label Header Encoding

- Labels containing commas, spaces, or quotes are enclosed in double quotes
- Non-ASCII label names are RFC 2047 encoded, as Takeout does, so the file stays 7-bit
- Long values are folded: RFC 5322 caps a line at 998 octets, and a thread with a handful of labels goes past that on one line
- Control characters in a label name are replaced with a space. `Label.name` is user input with no validation, and a raw CR/LF there would end the header and turn the rest of the name into forged headers, which a re-import would read back as real ones
- Labels containing double-quote characters are not currently supported for round-trip import

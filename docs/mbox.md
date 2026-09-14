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

Drafts written in Messages are not exported: they have no MIME representation yet (the body lives in a JSON blob). When there are any, the notification email counts them on their own line, apart from messages that were skipped because something went wrong. Drafts that came in through an import do have their MIME form, and are exported with `X-Status: T` and the `Drafts` label.

### Message Flags

Message flags are exported using the standard mbox `Status` and `X-Status` headers, which carry IMAP flags and nothing else:

| Header | Flag | Meaning |
|--------|------|---------|
| `Status: R` | R | Read (seen) |
| `Status: O` | O | Old (not recent) - always set for exports |
| `X-Status: F` | F | Flagged (starred) |
| `X-Status: T` | T | Draft |

**Examples:**
```text
Status: RO          # Read message
Status: O           # Unread message
X-Status: F         # Starred message
```

`X-Status: A` (Answered) is never written: it means "this message was replied to", which we do not track. It is in particular *not* a marker for sent mail.

`X-Status: D` (Deleted) is never written either, trashed messages included: mutt and Dovecot read it as `\Deleted` and purge the message for good on the next expunge. Trashed messages carry the `Trash` label instead.

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

Deliberately never written: `0x0008` Expunged, which means "deleted, pending folder compaction" and lets Thunderbird drop the message for good on the next compact. Trashed messages carry the `Trash` label instead. `0x0002` Replied and `0x1000` Forwarded are states we do not track.

`X-Mozilla-Keys` (Thunderbird's tags) is stripped on export but never written: its keys are IMAP mod-UTF-7 transformations of tag names, and a key absent from the reader's profile does not display as a tag anyway.

On import, `X-Mozilla-Status` is read back for the same two bits, which is the only way a Thunderbird mbox carries that state. Two restrictions apply:

- It is only read from file imports (mbox, eml). IMAP and PST report flags themselves and are authoritative, even for a message with no flag set at all.
- It is ignored when the message has an `X-Gmail-Labels` header. Takeout files and our own exports carry the mailbox state there, and an `X-Mozilla-Status` next to it came with the message from whoever sent it (Mozilla bug 196749).

Its position is not a signal: Thunderbird writes its own after `X-Account-Key` and `X-UIDL` for POP3 mail, and when a message already carries one, it keeps that one and updates it in place. Only the first occurrence is read, even when it is empty. In a plain `.eml` file or a third-party mbox, a sender-written header can therefore mark a message read or starred, exactly as it does in Thunderbird itself.

### Labels

Two headers are written, with different audiences:

| Header | Contents | Read by |
|--------|----------|---------|
| `X-Keywords` | The user's own labels only | OfflineIMAP, mu4e and other tools reading it comma-separated |
| `X-Gmail-Labels` | System labels, then the user's own labels, except those spelled like a system label | Google Takeout tooling, and our own importer |

A user label spelled like a system label (`Trash`, `Unread`, `Inbox`…) is left out of `X-Gmail-Labels`, where the importer would read it back as state, and so is one starting with `INBOX/` or `INBOX.`, which the importer strips as a folder prefix there. It still goes to `X-Keywords`, which the importer takes as plain labels, so it survives a roundtrip.

**Format:** comma-separated list, with quoted strings for labels containing commas, whitespace or double quotes (see [Label Header Encoding](#label-header-encoding)).

Dovecot's mbox driver reads `X-Keywords` as a *space*-separated list, so it would take `work,` and `important` as the keywords of `X-Keywords: work, important`. The comma format is kept for OfflineIMAP and mu, and for our own importer, which reads both.

```text
X-Keywords: work, important, "project alpha"
X-Gmail-Labels: Inbox, Opened, work, important, "project alpha"
```

### System Labels

mbox has no way to say which folder a message came from: `Status`/`X-Status` only carry IMAP flags, and IMAP has no per-message "sent" flag (a sent folder is a *mailbox* attribute, `\Sent` in RFC 6154). Google Takeout works around this with pseudo-labels in `X-Gmail-Labels`, and we do the same:

| Label | Written when |
|-------|--------------|
| `Drafts` | `is_draft` |
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
X-Mozilla-Status: 0005
X-Mozilla-Status2: 00000000
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

Labels from `X-Gmail-Labels` go through the special handling below. Labels from `X-Keywords` are always kept as plain labels: that header holds IMAP keywords, i.e. tags, never folders. Both are parsed by `gmail_labels()` in `core/mda/utils.py`, which handles:
- Comma-separated values: `label1, label2, label3`
- Space-separated values: `label1 label2 label3` (Dovecot format), used when no comma appears outside quoted strings, so `"Project, Q3" urgent` is two labels
- Quoted strings: `"label with spaces", simple-label`, where a backslash escapes the next character; anything unquoted is taken literally
- RFC 2047 encoded-words in `X-Gmail-Labels`, which Takeout uses for non-ASCII label names. `X-Keywords` is taken as is, as Dovecot does

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

A label in `X-Gmail-Labels` (or an IMAP/PST folder) that matches one of these names is consumed as state, so a label literally named `Sent` or `Trash` there does not survive as a label. In `X-Keywords` it does.

Sent mail and drafts are imported as read (and so is a message whose `From` is the mailbox it is imported into). For a file import (mbox, eml), an explicit `Opened` or `Unread` label in the message takes precedence, and our own exports always carry one. IMAP and PST imports keep the rule unconditionally.

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
| Imported drafts | `X-Gmail-Labels: Drafts` |
| Trashed / spam / archived | `X-Gmail-Labels: Trash` / `Spam` / `Archived` |
| Read / unread | `X-Gmail-Labels: Opened` / `Unread` |
| Starred | `X-Gmail-Labels: Starred` |

Message bodies come back byte for byte, including lines starting with `From ` and trailing empty lines (see [MBOX variant](#mbox-variant)).

Covered end to end in `core/tests/exporter/test_export_task.py` by `test_export_reimport_roundtrip_preserves_flags` (state) and `test_export_reimport_roundtrip_preserves_body_bytes` (content), both exporting a mailbox and re-importing it into another one.

Known limitations:

- Drafts written in Messages are not exported, so they cannot come back.
- A message whose last line has no line ending comes back with one: mbox cannot express its absence.
- Each label header carries at most 8 KiB of label names (dozens of 255-character labels, hundreds of ordinary ones); labels past that are left out of it. Without the cap, the importer's parser would reject the whole message (it refuses any header value over 100 KiB), and the `Date` it orders messages by would sit past the 64 KiB it reads.
- A label too long for one 998-octet `X-Keywords` line (only possible with multi-byte characters, a label being at most 255 of them) travels in `X-Gmail-Labels` alone. If it also starts with `INBOX/` or `INBOX.`, it does not come back.

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
- Each message ends with exactly one blank line of separation, added after its own last line, even when the body already ends with empty lines

Escaping the already-escaped forms is what makes mboxrd reversible: a reader strips exactly one `>` and gets the original bytes back. mboxo, which escapes only the bare `From `, cannot be reversed, because a line the author really did start with `>From ` is indistinguishable from an escaped one.

On import we strip one `>` from any `>+From ` line, and drop the blank line that separates two messages (the message's byte range runs up to the next `From ` line, so it ends with that blank line). Together with the export side, a message body survives a roundtrip byte for byte, which keeps DKIM body hashes valid. The header block does not: the metadata headers are added and stale copies removed (see [Header Injection](#header-injection)).

The one place where the mboxo ambiguity still bites is a **third-party mboxo file**, Google Takeout's among them: a body line that genuinely started with `>From ` there loses its `>`. Not unescaping at all is worse, since then every line the writer escaped keeps a `>` it never had.

### Line Endings

RFC 4155 says an mbox database MUST use a bare LF and MUST NOT use CRLF. We do not convert: messages are written with the line endings they were stored with, which for anything that arrived over SMTP means CRLF.

This is deliberate. Converting to LF is one-way (the original bytes cannot be restored on the way back in) and it breaks the DKIM body hash, so byte fidelity wins over conformance here. It also costs nothing in practice: Google Takeout writes CRLF too, so anything that reads a Takeout archive reads ours. Our own test fixture `core/tests/resources/messages.mbox` is a real Takeout file and contains zero bare LFs.

### Separator Detection

A postmark is a `From ` line with a sender (possibly empty) and a date: a weekday later followed by a month name and a digit, which covers ctime (`From <sender> Mon May 26 20:18:05 2025`, as Takeout, Thunderbird, mutt and Python write it) and RFC 822 dates (`Mon, 26 May 2025`), or an ISO 8601 timestamp. A bare sender line is `From ` followed by an address, `-` or `MAILER-DAEMON` alone. A header line is `<name>:`, with or without a space after the colon (`Subject:x` counts), but not `<name>://`, so that a URL is not taken for one.

A `From ` line is treated as a message separator when it is followed by a header line, and either holds:

1. It sits at the start of the file or directly after an empty line, and it is a postmark or a bare sender line
2. It sits anywhere else and is a postmark

The first separator of the file is the exception: at the start of the file or after an empty line, a postmark followed by any non-empty line is enough. There is no message yet for it to split, and rejecting it would drop its message.

Lines over 1000 bytes (line ending included) are never separators, since a real one is a sender and a date. Only their first 1000 bytes are read, which is enough to tell a header line, so the scan keeps bounded memory and linear time whatever the line lengths in the file.

Without these checks, a body paragraph beginning "From my point of view..." after a blank line splits the message in two, and the fragment is delivered as its own message with no error anywhere. That is the oldest bug in the format (Mozilla bug 355237). Rule 2 covers writers that put no empty line between messages: rejecting those separators would merge each message into the body of the previous one. Rejected `From ` lines are counted and logged once per file, since a writer that failed to escape them usually failed for all of them.

What cannot be told apart: an unescaped body line that is itself a genuine postmark (someone pasting a raw mbox excerpt, or prose with a word, then a weekday, a month and a number, like "From me Mon to Fri in May 2024") followed by a header-like line (`Note: read this`) splits the message, and so does a bare sender line followed by one after an empty line; a separator in none of these shapes is not recognised and its message merges into the previous one (both are logged); and in a file without empty separator lines, a body that ends on an empty line loses it.

### Header Injection

When exporting, the metadata headers (`Status`, `X-Status`, `X-Mozilla-Status`, `X-Mozilla-Status2`, `X-Keywords`, `X-Gmail-Labels`) are prepended at the top of the message, before the `Received:` chain, so the chain keeps its chronological descending order.

Any copy of those headers already present in the stored message is stripped first, along with `X-Mozilla-Keys`, and so are continuation lines at the very top that belong to no header, which would otherwise extend the last prepended one. A lone CR counts as a line break there, as it does for the message parser. Only the header block is touched. A message imported from Takeout or an IMAP server keeps the headers it arrived with, the mailbox state has moved on since, and the importer reads *every* occurrence of the label headers: without stripping, a re-import would resurrect stale labels and flags.

### Label Header Encoding

- Labels containing commas, whitespace, or double quotes are enclosed in double quotes, with `\` and `"` escaped by a backslash (RFC 5322 quoted-pair)
- Non-ASCII label names are RFC 2047 encoded in `X-Gmail-Labels`, as Takeout does, and so is a value containing `=?`, which would otherwise be decoded on import
- They stay raw UTF-8 in `X-Keywords`: keyword readers such as Dovecot take that header as is, and would see an encoded value as an opaque keyword
- Long values are folded: RFC 5322 caps a line at 998 octets, and a thread with a handful of labels goes past that on one line. Folding happens between labels (or between encoded-words), never inside one: unfolding on import collapses a run of spaces a fold lands on
- Control characters in a label name (C0 and C1, and the Unicode line and paragraph separators) are replaced with a space. `Label.name` is user input with no validation, and a raw CR/LF there would end the header and turn the rest of the name into forged headers, which a re-import would read back as real ones

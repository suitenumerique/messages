# jmap-email

A strict-JMAP RFC 8621 Email object library for Python 3.14+ with
lenient RFC 5322 / MIME parsing and strict-by-design composition.
**Zero runtime dependencies.**

This is a **production parser**, battle-hardened against years of
real-world inbound MIME — every CVE / paper listed in the
[defense matrix](#defense-matrix) below has a regression test.
The codebase came out of operating a high-volume inbound pipeline
and ships with the hardening baked in.

> Status: **beta** while the public API stabilizes (currently
> `0.1.0`). Wire shape conforms to RFC 8621 §4 today; future
> releases will only add fields, never break existing ones.

## Why a Python 3.14.5 floor?

The standard library `email` package receives frequent bug fixes
between patch releases, and this library wraps it directly — every
fix to header parsing, RFC 2047 encoded-words, address-list
defects, etc. surfaces immediately in our output. The 3.14.5 floor
is not arbitrary: it carries
[gh-128110](https://github.com/python/cpython/issues/128110)
(RFC 2047 §6.2 encoded-word adjacent-pair spacing under modern
policies), which materially affects the composer.

**We strongly encourage every production app to align on the
latest 3.14.x patch promptly.** Each CPython patch release that
touches `email` is one less class of malformed-input edge case
your inbox / archive / forensics pipeline needs to manually paper
over.

## Quick start

```bash
pip install jmap-email
```

```python
import jmap_email

# Parse raw RFC 5322 bytes → JMAP Email object dict
email = jmap_email.parse_email(raw_bytes)
email["subject"]        # str | None  (NFC normalized)
email["from"]           # [{"name": str | None, "email": str}, ...] | None
email["sentAt"]         # ISO-8601 with offset, e.g. "2026-06-05T14:30:00+02:00"
email["textBody"]       # JMAP EmailBodyPart[]
email["headers"]        # [{"name": "<wire-case>", "value": "<raw>"}, ...]
email["hasAttachment"]  # bool

# Strict-by-design composer accepts the same JMAP shape on input
raw = jmap_email.compose_email({
    "from": [{"name": "Alice", "email": "alice@example.com"}],
    "to": [{"name": "Bob", "email": "bob@example.com"}],
    "subject": "hi",
    "textBody": [{"partId": "1", "type": "text/plain", "content": "hello"}],
})

# Reply / forward templates (returns Email object dicts, NOT bytes)
reply = jmap_email.make_reply(email, body_text="thanks!")
fwd = jmap_email.make_forward(email, body_text="see below")
```

## Conformance

`parse_email()` produces a JMAP Email object per RFC 8621 §4, with the
following defaults and opt-ins:

| Property            | Default emitted? | Notes                                  |
| ------------------- | ---------------- | -------------------------------------- |
| Email metadata (`id`, `blobId`, `threadId`, `mailboxIds`, `keywords`, `size`, `receivedAt`) | No | Server-set; out of parser scope |
| `subject`           | Yes              | NFC-normalized; `null` when absent     |
| `from` / `sender` / `to` / `cc` / `bcc` / `replyTo` | Yes | `EmailAddress[]` or `null` |
| `messageId` / `inReplyTo` / `references` | Yes | `String[]` (no `<>`) or `null` |
| `sentAt`            | Yes              | ISO-8601 with offset; `null` when absent |
| `headers`           | Yes              | `[{name, value}]` ordered, wire-case   |
| `textBody` / `htmlBody` / `attachments` | Yes | `EmailBodyPart[]` |
| `hasAttachment`     | Yes              |                                        |
| `preview`           | Opt-in           | `parse_email(raw, preview=True)`       |
| `bodyValues`        | Opt-in           | `parse_email(raw, body_values=True)`   |
| `bodyStructure`     | Opt-in           | `parse_email(raw, body_structure=True)`|
| Per-part `headers`, `language`, `location`, `subParts` | No | Documented out of subset |

When `include_extensions=True` (default), the output also carries an
`ext` sub-dict with project-useful but non-JMAP fields:

- `ext.defects` — stdlib `MessageDefect` class names; useful for
  message-store quarantine policies (the Mailman pattern).
- `ext.gmailLabels` — labels extracted from `X-Gmail-Labels` /
  `X-Keywords` (Google Takeout / Dovecot / OfflineIMAP archives).
- `ext.headersBlocks` — headers split into trust-scope blocks
  delimited by `Received:` lines; convenient for spam classifiers.

Pass `include_extensions=False` for strict-JMAP-only output.

## Resource limits

The parser enforces hard caps against adversarial input:

| Constant                    | Default | Source                                   |
| --------------------------- | ------- | ---------------------------------------- |
| `_MAX_MIME_NESTING_DEPTH`   | 100     | Postfix `mime_nesting_limit`             |
| `_MAX_MIME_PARTS`           | 1000    | Go `multipartmaxparts`                   |
| `_MAX_HEADER_VALUE_BYTES`   | 102 400 | Postfix `header_size_limit`              |
| `_MAX_ADDRESS_LIST_BYTES`   | 100 000 | Dovecot CVE-2024-23184 analogue          |

Excess input is silently truncated and logged at WARNING level.

## Defense matrix

The parser explicitly defends against the documented attack classes
below. See `tests/test_parser.py` for regression coverage of each.

- **CVE-2023-27043** — `parseaddr`/`getaddresses` display-name confusion
- **CVE-2024-6923** — header-injection via embedded newlines (compose)
- **CVE-2024-21742** — Apache James `\r\n` in fields
- **CVE-2024-23184** — Dovecot unbounded address-list allocation
- **CVE-2002-1337** — Sendmail `crackaddr` nested-comments shape
- **CVE-2002-2325** — Pine empty-boundary infinite loop
- **gh-114906**     — embedded newline in RFC 2047 encoded-word
- **gh-136063**     — quadratic-time hot spots in `_header_value_parser`
- **gh-137687**     — base64 padding `==` truncation
- **PortSwigger "Splitting the Email Atom"** (DEF CON 32 2024) —
  encoded-word smuggling of structural chars (`@`, `,`, `<`, `>`, NUL)
- **Inbox Invasion (CCS '24)** — duplicate boundary parser confusion
- **Mailsploit** — NUL-byte truncation in encoded-words
- **USENIX 2020 "Weak Links in Auth Chains"** — duplicate `From:`,
  group-syntax, CFWS-in-address handling

## Strict-compose, lenient-parse

The two entry points use **different stdlib `email.policy` instances
on purpose**:

| Direction | Policy | Why |
|---|---|---|
| **Compose** (`compose_email`) | `email.policy.SMTP` (cloned, CTE 7-bit) | Caller-controlled input → must produce strictly RFC-compliant output. Enforces address-list folding, RFC 2047 / 2231 encoding, CRLF, line-length limits. |
| **Parse** (`parse_email`)     | `email.policy.compat32`                 | Real-world inbound MIME violates the spec routinely. `compat32` is lenient: it returns raw header strings and recovers what it can from broken Content-Transfer-Encoding, missing charsets, malformed structural delimiters. |

## License

MIT — see `LICENSE`.

## Versioning

Semantic. Public API is everything exported in `jmap_email.__all__`;
anything prefixed with `_` is internal and may change between
patch releases.

`__version__` is exposed at the module level.

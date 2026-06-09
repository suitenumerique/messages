# jmap-email

A strict-JMAP RFC 8621 Email object library for Python 3.14+, with
lenient RFC 5322 / MIME parsing and strict-by-design composition.
**Zero runtime dependencies** — the package is a clean wrapper around
the Python stdlib `email` package, plus null-safe shape accessors over
the JMAP Email object.

The codebase came out of operating an inbound mail pipeline; every CVE
and research result in the [defense matrix](#defense-matrix) below has
a regression test under `tests/`.

> Status: **beta** while the public API stabilizes. Wire shape
> conforms to RFC 8621 §4 today; future 0.1.x releases will only add
> fields, never remove or rename them.

## Why a Python 3.14.5 floor?

The standard library `email` package receives frequent bug fixes
between patch releases, and this library wraps it directly — every fix
to header parsing, RFC 2047 encoded-words, address-list defects, etc.
surfaces immediately in our output. The 3.14.5 floor is not arbitrary:
it carries
[gh-128110](https://github.com/python/cpython/issues/128110)
(RFC 2047 §6.2 encoded-word adjacent-pair spacing under modern
policies), which materially affects the composer.

**Aligning on the latest 3.14.x patch is recommended for any
production deployment.** Each CPython patch release that touches
`email` is one less class of malformed-input edge case downstream
pipelines need to paper over manually.

## Quick start

```bash
pip install jmap-email
```

```python
import jmap_email

# Parse raw RFC 5322 bytes → JMAP Email object dict (RFC 8621 §4),
# or None when the input is fundamentally unparseable (empty, non-bytes,
# stdlib produced no Message, etc.). parse_email never raises — the
# failure mode is a single `is None` check at the call site.
email = jmap_email.parse_email(raw_bytes)
if email is None:
    ...  # log + skip / 400 / quarantine — caller's choice

# Recoverable damage (a salvageable malformed header, an unknown
# charset that fell back to utf-8/replace, …) surfaces in
# email["_ext"]["defects"] when you opt into the project-extension
# namespace:
email_with_ext = jmap_email.parse_email(raw_bytes, extensions=True)
defects = (email_with_ext or {}).get("_ext", {}).get("defects") or []
email["subject"]        # str | None  (NFC normalised)
email["from"]           # [{"name": str | None, "email": str}, ...] | None
email["sentAt"]         # ISO-8601 with offset, e.g. "2026-06-08T14:30:00+02:00"
email["textBody"]       # JMAP EmailBodyPart[]
email["bodyValues"]     # {partId: {"value", "isEncodingProblem", "isTruncated"}}
email["headers"]        # [{"name": "<wire-case>", "value": "<raw>"}, ...]
email["hasAttachment"]  # bool
email["preview"]        # str  (≤256 chars, plain-text)

# Strict-by-design composer accepts the same JMAP shape on input.
# sentAt is required (RFC 5322 §3.6.1) — pass it explicitly.
raw = jmap_email.compose_email({
    "from": [{"name": "Alice", "email": "alice@example.com"}],
    "to": [{"name": "Bob", "email": "bob@example.com"}],
    "subject": "hi",
    "sentAt": "2026-06-08T12:00:00+00:00",
    "textBody": [{"partId": "1", "type": "text/plain", "content": "hello"}],
})
# raw is RFC 5322 bytes ready for SMTP delivery (e.g.
# smtplib.SMTP.sendmail handles dot-stuffing for you).
```

## Conformance

`parse_email()` produces a JMAP Email object per RFC 8621 §4 with the
following defaults, matching `Email/get` `defaultProperties`:

| Property            | Default emitted? | Notes                                  |
| ------------------- | ---------------- | -------------------------------------- |
| Email metadata (`id`, `blobId`, `threadId`, `mailboxIds`, `keywords`, `size`, `receivedAt`) | No | Server-set; out of parser scope |
| `subject`           | Yes              | NFC-normalised; `null` when absent     |
| `from` / `sender` / `to` / `cc` / `bcc` / `replyTo` | Yes | `EmailAddress[]` or `null` |
| `messageId` / `inReplyTo` / `references` | Yes | `String[]` (no `<>`) or `null` |
| `sentAt`            | Yes              | ISO-8601 with offset; `null` when absent |
| `headers`           | Yes              | `[{name, value}]` ordered; `value` is RFC 8621 Raw form (byte-faithful, NOT encoded-word-decoded) |
| `textBody` / `htmlBody` / `attachments` | Yes | `EmailBodyPart[]` per RFC 8621 §4.1.4 |
| `hasAttachment`     | Yes              |                                        |
| `preview`           | Yes              | ≤256-char plain-text excerpt; HTML-stripped + whitespace-normalised |
| `bodyValues`        | Yes              | `{partId: EmailBodyValue}` per §4.1.5; text-body parts then carry metadata only |
| `bodyStructure`     | Opt-in           | `parse_email(raw, body_structure=True)` |
| `_ext`               | Opt-in           | `parse_email(raw, extensions=True)` — project extensions; see below |

Parser-only fields (`preview`, `bodyValues`, `bodyStructure`,
`hasAttachment`, `ext`) are ignored on composer input — passing them
through `compose_email` is harmless.

### Project extensions (`ext`)

`extensions=True` adds a single `_ext` sub-dict to the output.
These fields are NOT in RFC 8621 — they expose information the parser
already computes so consumers don't have to re-walk the message:

- `_ext.defects` — stdlib `MessageDefect` class names collected during
  the parse walk; useful for message-store quarantine policies (the
  Mailman pattern).
- `_ext.resent` — Resent-* typed projection (see below). Present only
  when the wire carries at least one Resent-* header.

### `EmailBodyPart` extensions

RFC 8621 §4.1.4 lists the `EmailBodyPart` shape as `partId`, `blobId`,
`size`, `headers`, `name`, `type`, `charset`, `disposition`, `cid`,
`language`, `location`, `subParts`. The library extends that shape
with two project fields. Where each shows up:

| Location               | `content`                | `sha256` |
|------------------------|--------------------------|----------|
| `attachments[i]`       | always (`bytes`)         | always   |
| `textBody[i]` / `htmlBody[i]` with `body_values=False` | yes (`str` for text/*, base64 `str` for inline media) | no |
| `textBody[i]` / `htmlBody[i]` with `body_values=True`  | absent — content moves to `bodyValues` per §4.1.4 | no |
| `bodyStructure` and its `subParts` tree                | never                    | never    |

- `content` exists because the library has no blob store to satisfy
  the spec's `blobId` → fetch-by-blob contract. Callers need the
  bytes somewhere on the part. Attachment `content` is never
  stripped; text/html `content` follows the `body_values` flag.
- `sha256` is the hex digest of the part's decoded bytes — useful
  for dedup / blob storage. Attachment parts only.

`bodyStructure` is pure RFC 8621 shape — no project fields appear
in that tree, so a strict JMAP consumer can ingest it as-is. Strict
consumers should ignore unknown keys elsewhere. Composer input that
includes these fields is harmless — the composer ignores parser-only
metadata.

### Duplicate scalar headers

RFC 5322 §3.6 marks From / Sender / Reply-To / To / Cc / Bcc /
Message-ID / In-Reply-To / References / Subject / Date as `max=1` —
each may appear at most once. Real-world senders sometimes emit
duplicates anyway. The parser follows the stdlib
`email.message.Message[name]` convention: when a header is repeated,
the first occurrence wins for the scalar JMAP projection. Every
occurrence still appears in the `headers` list in document order.
Background: see "Detection of Weak Links in Authentication Chains",
USENIX Security 2020.

### Resent-* projection (`_ext.resent`)

RFC 8621 §4.1.3 names only the 11 base header convenience properties;
Resent-* is not on that list. The library pre-computes it as a §4.1.2
typed-projection idiom and exposes it under `_ext.resent` so forwarded /
resent mail handling doesn't need to walk `parsed["headers"]`. Sub-
fields mirror the base properties — `ext.resent["from"]`,
`["sender"]`, `["replyTo"]`, `["to"]`, `["cc"]`, `["bcc"]`,
`["messageId"]`, `["date"]` — and the sub-dict is omitted entirely
when no Resent-* header is present on the wire.

### Pragmatic deviations from RFC 8621

Two places where the parser knowingly deviates from the spec text.
Both are conscious choices for downstream safety; flagging them so
the contract is explicit:

- **`headers[i].value` is not strictly "Raw" form.** RFC 8621 §4.1.2
  defines "Raw" as byte-faithful except for `CRLF+WSP` unfolding.
  We additionally:
  - Strip NUL (`\x00`) bytes — PostgreSQL `TEXT` cannot store NUL, so a
    spec-faithful value would crash any downstream insert. Carrying
    them through and dropping them at the storage boundary would also
    be wrong (different stores would handle them differently).
  - Truncate at `max_header_value_bytes` (default 102 400) — the stdlib
    `_header_value_parser` has quadratic-time hot spots on adversarial
    inputs (gh-136063); truncating early bounds wall-clock.
  The `EmailBodyPart.headers[i].value` field follows the same policy.

- **Inline media isn't added to `attachments` in the `multipart/alternative`
  nullified-branch case.** The spec algorithm in §4.1.4 has a clause
  `if ((!htmlBody || !textBody) && isInlineMediaType(part)) attachments.push(part)`.
  We don't honor it. Effect: in the narrow case where a `multipart/
  alternative` ancestor has nullified one body branch and the message
  contains inline `image/*` / `audio/*` / `video/*`, the inline media
  appears in the surviving body but not in `attachments`. Matches what
  Gmail / Apple Mail render; differs from a strict spec walker.

## Resource limits

The parser enforces hard caps against adversarial input. Caps are
passed per-call via a frozen `ParseLimits` instance; the default
applies when no value is supplied.

| Attribute                    | Default | Source                                   |
| ---------------------------- | ------- | ---------------------------------------- |
| `max_mime_nesting_depth`     | 100     | Postfix `mime_nesting_limit`             |
| `max_mime_parts`             | 1000    | Go `multipartmaxparts`                   |
| `max_header_value_bytes`     | 102 400 | Postfix `header_size_limit`              |
| `max_address_list_bytes`     | 100 000 | Dovecot CVE-2024-23184 analogue          |

Excess input is silently truncated and logged at WARNING level.

A single process can host multiple workloads with different caps —
the limits travel with the call, never via shared module state:

```python
from jmap_email import ParseLimits, parse_email

bulk = ParseLimits(max_mime_parts=5000, max_mime_nesting_depth=200)
gateway = ParseLimits(max_mime_parts=500)

parse_email(big_archive_message, limits=bulk)
parse_email(inbound_smtp_bytes,  limits=gateway)
```

`ParseLimits` is frozen and hashable; instances can be reused freely
across threads and as cache keys.

## Strict-compose, lenient-parse

The two entry points use **different stdlib `email.policy` instances
on purpose**:

| Direction | Policy | Why |
|---|---|---|
| **Compose** (`compose_email`) | `email.policy.SMTP` (cloned, CTE 7-bit) | Caller-controlled input → must produce strictly RFC-compliant output. Enforces address-list folding, RFC 2047 / 2231 encoding, CRLF, line-length limits. |
| **Parse** (`parse_email`)     | `email.policy.compat32`                 | Real-world inbound MIME violates the spec routinely. `compat32` is lenient: it returns raw header strings and recovers what it can from broken Content-Transfer-Encoding, missing charsets, malformed structural delimiters. |

### Parser failure mode

`parse_email` is total: it returns a `JmapEmail` dict on success or
`None` on fundamental failure (empty bytes, wrong type, stdlib
producing no `Message`, or any unhandled internal error). All failures
log at WARNING level. No exception escapes.

```python
parsed = parse_email(raw)
if parsed is None:
    logger.warning("dropped unparseable message")
    return
...  # use parsed
```

Recoverable damage (a salvageable malformed header, an unknown
charset, etc.) keeps the parse on track — those are surfaced in
`parsed["_ext"]["defects"]` when the caller opts in via
`parse_email(raw, extensions=True)`.

### Composer error hierarchy

`compose_email` raises a typed exception that subclasses `ComposeError`.
Callers that don't want to discriminate can catch `ComposeError` only;
callers that do can dispatch on the subclass:

```text
ComposeError
├── InvalidAddressError       # missing/malformed `from`, `to`, …
├── InvalidMessageIdError     # Message-ID / In-Reply-To / References / Content-ID
├── InvalidDateError          # `sentAt` missing or unparseable
├── AttachmentError           # missing content, bad base64, bad MIME type, …
└── HeaderInjectionError      # custom-header name not RFC 5322 ftext
```

The composer is strict on every input the caller controls. Silently
substituting `now()` for a missing `sentAt`, or quietly dropping a
broken attachment, would be invisible data loss for the sender.

- Want "now" for `sentAt`? Use the `now_sent_at()` helper:
  `compose_email({..., "sentAt": now_sent_at(), ...})`.
- Handling flaky attachment input? Wrap the compose call in
  `try / except ComposeError` (the base class catches every
  composer error subclass — `InvalidAddressError`,
  `AttachmentError`, etc. — at once).

## Shape helpers

Every JMAP field is a list — `from`, `to`, `messageId`, `headers`, …
Reading them safely usually means writing `parsed.get("from") or []`,
then indexing, then `.get`. Skip that with these helpers:

```python
from jmap_email import (
    first_address, first_address_email, first_address_name,
    first_msgid, msgid_chain, sent_at_to_datetime,
    find_header, find_headers, has_header,
    body_part_text, body_text_joined,
)
```

About `body_part_text(parsed, part)`: a text body part can have its
text stored two ways depending on how `parse_email` was called. Either
the text is right on the part (`part["content"]`), or it's in a
separate map (`parsed["bodyValues"][part["partId"]]["value"]`). This
helper checks both, so your code keeps working if the parser default
ever flips.

About `now_sent_at()`: returns the current UTC time formatted as the
ISO-8601 string `compose_email` expects for `sentAt`. One-liner instead
of `datetime.now(timezone.utc).isoformat()`.

## Validators

Want to know if a string would be accepted by `compose_email` as a
Message-ID without actually trying to compose? Use `is_valid_msg_id`:

```python
from jmap_email import is_valid_msg_id

if is_valid_msg_id(parent_header):
    reply["inReplyTo"] = [parent_header]
```

It applies exactly the same checks `compose_email` does — shape,
length ceiling, no embedded whitespace — but returns `True`/`False`
instead of raising. Useful for lenient parse paths (archive importers,
inbound salvaging) that need to decide between keeping a raw id and
falling back to synthesis without catching an exception.

## Strict vs. lenient `parse_address`

`parse_address(s)` is **strict by default**: an input that can't be
parsed into a valid addr-spec returns `("", "")`. Use this for entry-
point validation (CLI flags, web form input) — `parse_address("no-at")`
returning `("", "")` lets the caller reject garbage without a second
`"@" in result` check.

Pass `lenient=True` for archive-import paths that must preserve the
original wire bytes even when invalid:

```python
parse_address("no-at-sign")               # → ("", "")
parse_address("no-at-sign", lenient=True) # → ("", "no-at-sign")
```

`parse_addresses(s)` is always strict per-entry: tuples whose addr-spec
fails the shape check are silently dropped — so
`len(parse_addresses(header)) != header.count(",") + 1` is expected
when the header carries garbage between real entries.

## Defense matrix

The parser explicitly defends against the documented attack classes
below. See the `tests/` directory for regression coverage of each.

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

## Compatibility

- **Python** 3.14.5+ (see [Why a Python 3.14.5 floor?](#why-a-python-3145-floor))
- **Platforms tested in CI:** Linux on x86_64 and arm64
- **macOS / Windows / PyPy / free-threaded build:** untested; expected
  to work since the package has zero compiled extensions and zero
  runtime dependencies. Reports of breakage welcome via the issue
  tracker.

## Performance and concurrency

- **Thread-safe** at the public API level. Module-level state
  (`_HEADER_FACTORY`, `_POLICY`) is constructed once at import and
  never mutated after.
- **No I/O.** Every entry point operates on in-memory bytes or dicts.
- **No global rate limits or singletons** beyond the immutable
  registries above. Multiple processes / asyncio tasks may call
  `parse_email` / `compose_email` concurrently without coordination.

Ballpark wall time on an Apple M2 (single thread, in-process):
≈ 0.4 ms per typical 5 kB inbound message; ≈ 1 ms per 100 kB MIME
multipart with embedded images. Use your own corpus to measure for
your workload — message-shape variation dominates.

## Examples

Runnable scripts under `examples/`:

- `examples/parse_and_print.py` — parse raw bytes and pretty-print the
  JMAP shape
- `examples/import_eml_safely.py` — read an `.eml` off disk, handle
  the `None` failure path, surface defects, print key fields
- `examples/compose_with_attachment.py` — compose a multipart message
  with a regular attachment
- `examples/inline_image_roundtrip.py` — compose + re-parse a message
  with an inline image, asserting the CID survives
- `examples/encoded_word_subject.py` — compose a non-ASCII Subject
  and re-parse it

## Development

The repository ships a docker-compose-based test environment so the
package can be exercised against the exact Python / pytest / hypothesis
versions CI uses:

```bash
make test-jmap-email        # run the full test suite (zero infra deps)
make typecheck-jmap-email   # static check via Astral's `ty` (Rust)
```

To run tests outside docker:

```bash
cd src/jmap-email
pip install -e '.[dev]'
pytest                       # default selection, fuzz tests excluded
pytest -m fuzz               # property-based / Hypothesis fuzz
ruff check .
ruff format --check .
```

See `CONTRIBUTING.md` for the contribution workflow.

## License

MIT — see `LICENSE`.

## Versioning

Semantic. Public API is everything exported in `jmap_email.__all__`;
anything prefixed with `_` is internal and may change between patch
releases.

`__version__` is exposed at the module level.

## Security

Security-sensitive reports go through GitHub Security Advisories — see
`SECURITY.md` for the disclosure policy.

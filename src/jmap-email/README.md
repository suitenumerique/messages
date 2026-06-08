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

# Parse raw RFC 5322 bytes → JMAP Email object dict (RFC 8621 §4)
email = jmap_email.parse_email(raw_bytes)
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

# Reply / forward templates (return Email object dicts, not bytes).
# The caller fills in `from` and `sentAt` before composing.
reply = jmap_email.make_reply(email, body_text="thanks!")
fwd = jmap_email.make_forward(email, body_text="see below")
```

## Conformance

`parse_email()` produces a JMAP Email object per RFC 8621 §4 with the
following defaults, matching `Email/get` `defaultProperties`:

| Property            | Default emitted? | Notes                                  |
| ------------------- | ---------------- | -------------------------------------- |
| Email metadata (`id`, `blobId`, `threadId`, `mailboxIds`, `keywords`, `size`, `receivedAt`) | No | Server-set; out of parser scope |
| `subject`           | Yes              | NFC-normalised; `null` when absent     |
| `from` / `sender` / `to` / `cc` / `bcc` / `replyTo` | Yes | `EmailAddress[]` or `null` |
| `resentFrom` / `resentSender` / `resentReplyTo` / `resentTo` / `resentCc` / `resentBcc` | Yes | RFC 8621 §4.1.3 typed projections |
| `messageId` / `inReplyTo` / `references` / `resentMessageId` | Yes | `String[]` (no `<>`) or `null` |
| `sentAt` / `resentDate` | Yes          | ISO-8601 with offset; `null` when absent |
| `headers`           | Yes              | `[{name, value}]` ordered; `value` is RFC 8621 Raw form (byte-faithful, NOT encoded-word-decoded) |
| `textBody` / `htmlBody` / `attachments` | Yes | `EmailBodyPart[]` per RFC 8621 §4.1.4 |
| `hasAttachment`     | Yes              |                                        |
| `preview`           | Yes              | ≤256-char plain-text excerpt; HTML-stripped + whitespace-normalised |
| `bodyValues`        | Yes              | `{partId: EmailBodyValue}` per §4.1.5; text-body parts then carry metadata only |
| `bodyStructure`     | Opt-in           | `parse_email(raw, body_structure=True)` |

Parser-only fields (`preview`, `bodyValues`, `bodyStructure`,
`hasAttachment`, `ext`) are ignored on composer input — passing them
through `compose_email` is harmless.

When `include_extensions=True` (default), the output also carries an
`ext` sub-dict surfacing parser-internal information that doesn't
belong to the JMAP wire shape:

- `ext.defects` — stdlib `MessageDefect` class names collected during
  the parse walk; useful for message-store quarantine policies (the
  Mailman pattern).

Pass `include_extensions=False` for strict-JMAP-only output.

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

### Resent-* projections

The Resent-* convenience properties surface from the canonical
`Resent-From` / `Resent-Sender` / `Resent-Reply-To` / `Resent-To` /
`Resent-Cc` / `Resent-Bcc` / `Resent-Message-ID` / `Resent-Date`
headers. Consumers don't need to walk `headers` manually to handle
forwarded or resent mail.

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

### Composer error hierarchy

`compose_email` raises a typed exception that subclasses `ComposeError`.
Callers that don't want to discriminate can catch `ComposeError` only;
callers that do can dispatch on the subclass:

```text
ComposeError
├── InvalidAddressError       # missing/malformed `from`, `to`, …
├── InvalidMessageIdError     # Message-ID / In-Reply-To / References / Content-ID
├── InvalidDateError          # `sentAt` missing or unparseable
├── AttachmentError           # base64 decode failure, …
└── HeaderInjectionError      # custom-header name not RFC 5322 ftext
```

The composer is strict-by-design on `sentAt`: a missing or
unparseable value raises `InvalidDateError` rather than substituting
`now()`. Callers that genuinely want "now" pass
`datetime.now(timezone.utc)` explicitly.

## Shape helpers

`jmap_email` ships null-safe accessors over the JMAP Email shape so
downstream code doesn't repeat `parsed.get("from") or []` + index +
`.get` chains:

```python
from jmap_email import (
    first_address, first_address_email, first_address_name,
    first_msgid, msgid_chain, sent_at_to_datetime,
    find_header, find_headers, has_header,
    body_part_text, body_text_joined,
)
```

`body_part_text(parsed, part)` is transparent to the `bodyValues`
projection: it reads the inline `content` when present, falls through
to `parsed["bodyValues"][partId]["value"]` otherwise. Future flips of
the parser default won't break consumers.

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
- `examples/compose_with_attachment.py` — compose a multipart message
  with a regular attachment
- `examples/inline_image_roundtrip.py` — compose + re-parse a message
  with an inline image, asserting the CID survives
- `examples/reply_with_threading.py` — build a reply that preserves
  `In-Reply-To` / `References` threading
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

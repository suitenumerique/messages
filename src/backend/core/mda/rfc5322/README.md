# RFC 5322 email parser & composer

This module handles RFC 5322 / 5321 / 2047 / 2231 email messages on both the
inbound and outbound paths. It exposes two complementary entry points:

- `composer.compose_email(jmap_data)` — outbound: build raw RFC 5322 bytes from
  a JMAP-style dict.
- `parser.parse_email_message(raw_bytes)` — inbound: parse raw RFC 5322 bytes
  into the same JMAP-style dict shape.

Both sides use the JMAP body conventions (`textBody`, `htmlBody`,
`attachments`) so callers don't need to know which way data is flowing.

## Strict compose, lenient parse

The two entry points use **different libraries on purpose**, and the asymmetry
is the point.

| Direction | Backend | Why |
|---|---|---|
| **Compose** (`composer.py`) | Python stdlib `email` | Caller-controlled input → must produce strictly RFC-compliant output. Stdlib's `email.policy.SMTP` enforces correct address-list folding, RFC 2047 encoded-word emission, RFC 2231 parameter encoding, and CRLF / line-length limits. No third-party correctness bugs to inherit. |
| **Parse** (`parser.py`) | Mailgun's Flanker | Real-world inbound MIME comes from every MTA on the planet, including ones that violate the spec. Flanker's lenient parser recovers from common malformations (encoded structural delimiters, missing charsets, broken Content-Transfer-Encoding) where stdlib's strict policy would raise. |

### Compose: why stdlib

Until 2026 the composer was Flanker-backed. It carried two known security-flavor
bugs in Flanker's address-header serialization:

- `'; '.join(...)` was used as the address-list separator instead of `', '`,
  violating RFC 5322 §3.4 whenever any recipient had a non-ASCII display name.
- `ace_display_name` wrapped the display-name in `smart_quote()` *before*
  RFC 2047 encoding, leaking literal `"` characters into the encoded-word —
  enabling a class of address-list injection-via-display-name attacks.

Both were fixed in our flanker fork, but the broader pattern — Flanker is
unmaintained upstream, so any new compose bug becomes ours to fix forever —
made stdlib the correct destination. CPython has been quietly fixing this
exact corner of the email spec for years (gh-100884, gh-118643, gh-121284,
gh-127794, gh-142006, gh-142517, gh-144156). On a Python ≥ 3.14.4 floor we
get all of those for free.

The composer requires **no fallback path**: caller-controlled input means we
should never see malformed inputs, and producing strictly compliant output is
the security-correct choice. Headers with embedded CR/LF are stripped
defensively (`_sanitize_header_value`) to defeat header injection.

### Parse: why Flanker (for now)

The parser stays on Flanker because:

- Real inbound messages contain encoding violations, malformed Content-Type
  parameters, charset mismatches, and broken structural delimiters that
  stdlib's `policy.default` rejects with `MessageDefect` errors.
- Flanker's `addresslib.address.parse_list` returns the survivors instead of
  the empty set when one address in a list is malformed (modulo the long-known
  upstream bug in mailgun/flanker#190 — orthogonal to our concerns here).
- Migrating the parser carries real regression risk against thousands of
  message fixtures we don't fully control.

The parser's public surface (`parse_email_message`, `parse_email_address`,
`parse_email_addresses`, `decode_email_header_text`, `parse_date`) does not
expose Flanker types — callers always see plain dicts and primitives — so a
future migration to stdlib + targeted lenience helpers (e.g. `policy.compat32`
fallback on `MessageDefect`) is straightforward and unblocked.

## JMAP shape

Both compose and parse use the [JMAP](https://jmap.io/spec-mail.html#properties-of-the-email-object)
email object shape:

- `from`, `to`, `cc`, `bcc` — `[{"name": str, "email": str}, ...]`
- `subject` — `str`
- `textBody`, `htmlBody` — `[{"partId": str, "type": str, "charset": str, "content": str}, ...]`
- `attachments` — `[{"name": str, "type": str, "content": base64-str, "disposition": "attachment" | "inline", "cid": str | None}, ...]`
- `messageId`, `headers`, `date` — as needed

## Usage

```python
from core.mda.rfc5322 import (
    compose_email, EmailComposeError,
    parse_email_message, EmailParseError,
)

# Outbound:
try:
    raw_bytes = compose_email({
        "from": {"name": "Alice", "email": "alice@example.com"},
        "to": [{"name": "Bob", "email": "bob@example.com"}],
        "subject": "hi",
        "textBody": [{"content": "hello"}],
    })
except EmailComposeError as e:
    ...

# Inbound:
try:
    parsed = parse_email_message(raw_bytes)
    subject = parsed["subject"]
    sender = parsed["from"]
    text_parts = parsed["textBody"]
except EmailParseError as e:
    ...
```

## Tests

- `test_rfc5322_composer.py` — compose-side: address formatting, MIME structure
  cases (text-only, html-only, alternative, related-with-inline, mixed-with-
  attachments), header-injection defense, ported regression cases from
  flanker's own composer test suite.
- `test_rfc5322_parser.py` — parse-side: address/header/date parsing, body
  extraction, malformed-input recovery.

## Dependencies

- Python ≥ 3.14.4 (composer relies on stdlib `email` fixes through 3.14.4 — gh-100884, gh-118643, gh-121284, gh-127794, gh-142006, gh-142517, gh-144156).
- Flanker (parser-only, pinned via fork in `pyproject.toml`).

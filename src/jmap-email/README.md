# jmap-email

A strict-JMAP RFC 8621 Email object library for Python 3.14+, with
lenient RFC 5322 / MIME parsing and strict-by-design composition.
**One runtime dependency** — the package is a clean wrapper around
the Python stdlib `email` package, plus null-safe shape accessors over
the JMAP Email object. The single dependency is
[`idna`](https://pypi.org/project/idna/), for UTS 46 non-transitional
domain encoding: the stdlib codec is IDNA2003, which silently folds
`faß.de` into the *distinct* registrable domain `fass.de`.

The codebase came out of operating an inbound mail pipeline; every CVE
and research result in the [defense matrix](#defense-matrix) below has
a regression test under `tests/`.

> Status: **beta** while the public API stabilizes. The wire shape
> conforms to RFC 8621 §4. Per SemVer, 0.x releases may still make
> breaking API changes — 0.2.0 renamed `ParseLimits` → `ParseOptions`
> and moved the `TypedDict` shapes to `jmap_email.types`; every breaking
> change is called out in the [CHANGELOG](CHANGELOG.md).

## Why a Python 3.14.6 floor?

The standard library `email` package receives frequent bug fixes
between patch releases, and this library wraps it directly — every fix
to header parsing, RFC 2047 encoded-words, address-list defects, etc.
surfaces immediately in our output. The 3.14.6 floor is not arbitrary:
it carries
[gh-128110](https://github.com/python/cpython/issues/128110)
(RFC 2047 §6.2 encoded-word adjacent-pair spacing under modern
policies), which materially affects the composer, plus the further
`email` fixes shipped in 3.14.6.

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
| `preview`           | Yes              | ≤256-char plain-text excerpt; HTML/MD-stripped + whitespace-normalised |
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
  the parse walk, plus the parser-ambiguity markers below; useful for
  message-store quarantine policies (the Mailman pattern).
- `_ext.resent` — Resent-* typed projection (see below). Present only
  when the wire carries at least one Resent-* header.

#### Parser-ambiguity markers

A single email is parsed several times on its way to a reader — by the
receiving server, by a spam filter, by a virus scanner, finally by the
client that displays it. Some MIME constructs are resolved differently
by each, and that gap is exploitable: a filter can be made to read past
content the client goes on to render. Differential fuzzing of Postfix,
SpamAssassin, ClamAV, Evolution and Thunderbird demonstrates working
smuggling on exactly these constructs.[^mime2025]

This parser resolves each the way the stdlib does and carries on. What
it will not do is hand you a confident parse without telling you a
choice was made, so each construct also lands in `_ext.defects`:

| Marker | Construct | Why it matters |
| --- | --- | --- |
| `DuplicateFromDefect` | more than one `From` | one identity for the filter that authenticated the message, another for the human who reads it (CERT VU#517845) |
| `DuplicateScalarHeaderDefect` | any other RFC 5322 §3.6 max=1 header twice | `Subject`, `Date`, `Message-ID`, … — first wins here, last may win elsewhere |
| `DuplicateContentTypeDefect` | more than one `Content-Type` | we take the first; a client honouring the second sees a multipart tree, and its attachments never reach you |
| `DuplicateTransferEncodingDefect` | more than one `Content-Transfer-Encoding` | filter and client can disagree on whether the body is encoded at all |
| `UnrecognizedTransferEncodingDefect` | token outside RFC 2045 §6.1 (`bas64`, `: base64`, …) | we leave the body undecoded; lenient clients guess and reveal it |
| `DuplicateBoundaryParameterDefect` | two `boundary=` parameters | whichever we honour, the other delimits parts we never see |
| `MissingMimeVersionDefect` | MIME syntax with no `MIME-Version` | a strict receiver reads the body as flat text and never sees the parts |
| `NonEmptyPreambleDefect` | text before the first boundary | RFC 2046 §5.1 says ignore it, and we do — Thunderbird and Evolution show it as the first line |
| `NonEmptyEpilogueDefect` | text after the closing boundary | the same gap at the other end of the body |
| `ConflictingAttachmentNameDefect` | part names itself twice, differently | `Content-Type: name=` vs `Content-Disposition: filename=` vs `filename*` — the recipient saves it under a name you never saw |
| `ControlCharInHeaderDefect` | NUL or control character in a MIME header | read three ways: stripped (us), truncated at it, or kept — one part, three filenames |
| `EmptyBoundaryDefect` | multipart with an absent or empty `boundary=` | no agreed way to split the body |
| `EncodedWordInParameterDefect` | RFC 2047 `=?…?=` in a MIME parameter | not permitted there (RFC 2231 is the mechanism); we decode it, others show it raw |
| `FoldInQuotedParameterDefect` | a header folds *inside* a quoted parameter value | `filename="pay<CRLF> load.exe"` — we unfold to `pay load.exe`, a parser dropping the whole fold reads `payload.exe`, one truncating at CR reads `pay` |
| `PartialMessageDefect` | `message/partial` | RFC 2046 §5.2.2 splits one message across several; the payload exists only after a reassembly a per-message scanner never does |
| `ExternalBodyDefect` | `message/external-body` | the content is fetched from elsewhere, so it is not in this message for anyone to scan |
| `AddressListTruncatedDefect` | address header past `max_address_list_bytes` | entries past the cut are dropped, so the list we report is shorter than the wire's — and empty (`[]`) when no mailbox separator precedes the cap. A recipient you never see is one you cannot act on |

None is an error, and well-formed mail raises none of them. Treat them
as input to a policy: score them, quarantine on them, or log them and
move on.

One related scope boundary, which is *not* a marker because it would
fire on every forwarded message: attachments nested inside a
`message/rfc822` part are not enumerated in the outer `attachments`
list. The nested message arrives whole, as one attachment carrying its
raw bytes in `content` — re-run `parse_email` on those bytes if you
scan attachments, or a payload one level down stays invisible to you.

The last three are the anomaly classes named by
[draft-chen-email-mime-ambiguity-defense][ietf-mime] that the stdlib does
not already flag for you.

[^mime2025]: S. B. Andarzian, M. Meyers, E. Poll, *Email Smuggling with
Differential Fuzzing of MIME Parsers*, Radboud University, 2025. See also
J. Chen et al., *Inbox Invasion: Exploiting MIME Ambiguities to Evade
Email Attachment Detectors*, CCS '24.

[ietf-mime]: https://datatracker.ietf.org/doc/draft-chen-email-mime-ambiguity-defense/

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
  - Refuse a field above `max_header_value_bytes` (default 102 400):
    `parse_email` returns `None` rather than truncating — there is no
    generally safe place to cut an arbitrary field (see the options
    table below). Bounding the input also sidesteps the stdlib
    `_header_value_parser`'s quadratic-time hot spots on adversarial
    inputs (gh-136063).
  The `EmailBodyPart.headers[i].value` field follows the same policy.

- **Inline media isn't added to `attachments` in the `multipart/alternative`
  nullified-branch case.** The spec algorithm in §4.1.4 has a clause
  `if ((!htmlBody || !textBody) && isInlineMediaType(part)) attachments.push(part)`.
  We don't honor it. Effect: in the narrow case where a `multipart/
  alternative` ancestor has nullified one body branch and the message
  contains inline `image/*` / `audio/*` / `video/*`, the inline media
  appears in the surviving body but not in `attachments`. Matches what
  Gmail / Apple Mail render; differs from a strict spec walker.

## Parse options

Per-call options are passed via a frozen `ParseOptions` instance; the
default applies when no value is supplied. Most are hard caps against
adversarial input, but the bundle also carries format policy the RFC
leaves to the server, such as the `preview` length.

| Attribute                    | Default | Source                                   |
| ---------------------------- | ------- | ---------------------------------------- |
| `max_mime_nesting_depth`     | 100     | Postfix `mime_nesting_limit`             |
| `max_mime_parts`             | 1000    | Go `multipartmaxparts`                   |
| `max_header_value_bytes`     | 102 400 | Postfix `header_size_limit`              |
| `max_address_list_bytes`     | 100 000 | Dovecot CVE-2024-23184 analogue          |
| `max_preview_chars`          | 256     | RFC 8621 §4.1.4 `preview` ceiling        |
| `max_preview_scan_bytes`     | 131 072 | bound `preview` work on markup-only input |

Most excess input is silently truncated and logged at WARNING level.

`max_header_value_bytes` is the exception: a field above it makes
`parse_email` return `None`. RFC 5322 §2.2.3 puts no limit on a header
field — "an unfolded header field has no length restriction and
therefore may be indeterminately long" — so this is local policy, set to
Postfix's `header_size_limit`. Postfix discards the excess; we refuse the
message, because there is no generally safe place to cut an arbitrary
field. A shortened `Received` still looks well-formed to trust-scope
logic, and cutting an address list mid-token *manufactures* an address
nobody sent. `max_address_list_bytes` truncation, which stays below that
ceiling, therefore cuts back to a top-level mailbox separator — the same
shape as Postfix's `header_address_token_limit`, which discards excess
*tokens* rather than bytes — and records
`AddressListTruncatedDefect`.

A single process can host multiple workloads with different options —
they travel with the call, never via shared module state:

```python
from jmap_email import ParseOptions, parse_email

bulk = ParseOptions(max_mime_parts=5000, max_mime_nesting_depth=200)
gateway = ParseOptions(max_mime_parts=500)

parse_email(big_archive_message, options=bulk)
parse_email(inbound_smtp_bytes,  options=gateway)
```

`ParseOptions` is frozen and hashable; instances can be reused freely
across threads and as cache keys. (Pre-0.2 this was `ParseLimits`, passed as
`limits=`; both were renamed outright — see the [CHANGELOG](CHANGELOG.md).)

`DEFAULT_PARSE_OPTIONS` is the instance used when you pass no `options=`.
Every field has a default, so you rarely need it to *build* options —
`ParseOptions(max_mime_parts=500)` already inherits the rest. It's there to
read the shipped values without constructing anything:

```python
from jmap_email import DEFAULT_PARSE_OPTIONS

budget = DEFAULT_PARSE_OPTIONS.max_preview_chars   # 256
```

## Compose options

`ComposeOptions` is the compose-side peer of `ParseOptions` — same frozen,
hashable, `dataclasses.replace`-able shape, passed the same way:

```python
from jmap_email import ComposeOptions, compose_email

raw = compose_email(jmap, options=ComposeOptions(idna_encode_domains=True))
```

Where `ParseOptions` is mostly resource caps against hostile input, these
are output-correctness choices — each names a place where "strict" is
deployment-dependent rather than universal.

| Field | Default | Effect |
| --- | --- | --- |
| `emit_bcc` | `False` | Emit the `Bcc:` header. The entire point of Bcc is that it must not be transmitted; set `True` only for archive reconstruction (PST import), where the list was already in the source. |
| `idna_encode_domains` | `False` | IDNA-encode a non-ASCII **domain** to its A-label form (`contact@exemplé.fr` → `contact@xn--exempl-gva.fr`). |
| `allow_8bit` | `False` | Emit non-ASCII bodies as raw 8-bit instead of base64/QP. Requires 8BITMIME (RFC 6152) on the hop. |
| `allow_smtputf8` | `False` | Emit UTF-8 headers (RFC 6532) and permit a non-ASCII **local part**. Requires SMTPUTF8 (RFC 6531) on the hop. Implies `allow_8bit`. |

The last two name ESMTP capabilities of the hop the bytes are headed
for. This library does not discover those, and does not assume them —
hence the conservative defaults, which produce pure-ASCII 7-bit output
that any MTA accepts. How you learn them is yours: a relay whose
configuration you own, an EHLO response you read *before* composing, or
two variants composed up front so the delivery loop can fall back.

`in_reply_to` and `prepend_headers` stay keyword arguments on
`compose_email`: they are per-*message* data that changes on every call,
where everything in `ComposeOptions` is a property of the call site.

### Non-ASCII addresses

`idna_encode_domains` governs the domain, and deliberately only the domain.
Punycode is a DNS algorithm (RFC 3492/5891) and a local part is not a DNS
label, so an IDN domain has an exact ASCII wire form — the same one the MX
lookup has to use — and a non-ASCII local part has none.

A non-ASCII **local part** therefore needs `allow_smtputf8`, not this
flag: with `allow_smtputf8=True` the whole addr-spec travels as UTF-8, and
without it the address raises `InvalidAddressError`. Carrying one requires
SMTPUTF8 (RFC 6531), which is negotiated per-hop against the receiver's
EHLO, is viral across every address in the transaction, and has **no
downgrade path** — RFC 6530 dropped the mechanism RFC 5504 had specified.
Support is also not transitive: a relay that accepts your transaction may
itself have to forward to a hop that doesn't. That is why the ASCII
fallback variant below still rejects such an address: there is no ASCII
form of it to fall back *to*.

Left at the default, a non-ASCII *domain* raises too, because the composer
does not rewrite an address you handed it unless you ask.

### Composing a fallback pair

Because SMTPUTF8 is per-hop and cannot be discovered ahead of time, the
usual pattern is to compose both forms and let the delivery loop choose:

```python
from jmap_email import ComposeOptions, ComposeError, compose_email

preferred = compose_email(jmap, options=ComposeOptions(allow_smtputf8=True))
try:
    fallback = compose_email(jmap, options=ComposeOptions(idna_encode_domains=True))
except ComposeError:
    fallback = None   # no ASCII form exists — see below

smtp.ehlo()
if smtp.has_extn("smtputf8"):
    smtp.sendmail(sender, rcpts, preferred, mail_options=["SMTPUTF8", "BODY=8BITMIME"])
elif fallback is not None:
    smtp.sendmail(sender, rcpts, fallback)
else:
    ...  # bounce: RFC 6530 provides no downgrade for a UTF-8 mailbox name
```

An ASCII fallback exists whenever only the *domain* is non-ASCII. When
the **local part** is non-ASCII there is none — RFC 6530 dropped the
downgrade mechanism RFC 5504 had specified — and `compose_email` raising
is how you find that out. Bouncing is the specified behaviour, not a
gap in this library.

Worth knowing what the standard library does here, since it is not this:
under `email.policy.default` it RFC 2047-encodes a non-ASCII domain,
emitting `contact@=?utf-8?q?exempl=C3=A9?=.fr`, which RFC 2047 §5 forbids
inside an addr-spec and no MTA routes. It never punycodes, at any layer —
`smtplib.send_message` buckets any non-ASCII address straight to SMTPUTF8
or raises `SMTPNotSupportedError`. Converting the domain is left to you.

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

The `TypedDict` shapes describing the return value (`JmapEmail`,
`EmailAddress`, `EmailBodyPart`, `EmailBodyValue`, `EmailHeader`,
`Attachment`, `JmapEmailExt`) are annotation-only and live in the
`jmap_email.types` submodule — `from jmap_email.types import JmapEmail`.

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

## Reading raw headers

`parsed["headers"]` is deliberately RFC 8621 **Raw** form — byte-faithful,
*not* encoded-word-decoded (see [Conformance](#conformance)). That keeps the
header list honest for anything that needs the wire bytes, and it means any
header you read yourself may still be RFC 2047 encoded, or be a date string
nobody has parsed. Two helpers close that gap:

```python
from jmap_email import decode_rfc2047_header, parse_date

raw   = find_header(parsed, "X-Gmail-Labels")   # "=?UTF-8?Q?Re=C3=A7us?="
label = decode_rfc2047_header(raw)              # "Reçus"

when = parse_date("Mon, 8 Jun 2026 14:30:00 +0200")   # datetime | None
```

`decode_rfc2047_header` handles the mixed-charset, multi-word case and
returns a plain `str`. It is hardened against the encoded-word attacks in
the [defense matrix](#defense-matrix) — gh-114906 embedded newlines and
Mailsploit NUL truncation — which is the reason to use it over calling
`email.header.decode_header` yourself.

`parse_date` returns `None` instead of raising on the malformed dates real
archives are full of, where the stdlib `parsedate_to_datetime` throws. Use
it for any date header you read raw; the `sentAt` field is already parsed
for you, and `sent_at_to_datetime` converts *that* back to a `datetime`.

## Preview extraction

`preview_text` turns an HTML or html2text-style body into the single,
display-ready line RFC 8621 §4.1.4 calls `preview`. `parse_email` already
runs it for the `preview` field (drawn from the HTML part, falling back to
the text part); call it directly for any other snippet need:

```python
from jmap_email import preview_text

line  = preview_text(body_text)                              # ≤ 256 chars
short = preview_text(body_text, max_chars=140)               # list-view snippet
html  = preview_text(body_html, content_type="text/html")    # HTML part
```

It strips HTML (dropping `<script>` / `<style>` / `<title>` / `<blockquote>`
payloads, decoding entities, unwrapping `<https://…>` autolinks), drops
`>`-quoted lines, strips markdown (ATX + setext headers, lists, emphasis,
links → label, …), and removes control characters, ANSI/terminal escape
sequences, and invisible "preheader spacer" format characters — collapsing
everything to one line. It's bounded on hostile input: the HTML strip stops
once it has the preview's worth of visible text, and `max_scan_bytes`
(128 KiB) caps a body that is almost entirely markup.

**Pass the part's `content_type`.** Two of those stages are conventions of the
plain-text wire format, not universal cleanups, so they are skipped for
`text/html`:

| Stage | `text/plain` | `text/html` |
| ----- | ------------ | ----------- |
| HTML strip (`script` / `style` / `title` / `blockquote` payloads) | yes | yes |
| `>`-quoted lines dropped | yes | no |
| markdown syntax stripped | yes | no |
| control chars + ANSI escapes removed | yes | yes |
| whitespace collapsed, truncated | yes | yes |

In an HTML part, `>`, `*` and `_` are literal characters the sender meant to
be shown, and quoted history arrives as `<blockquote>` (already suppressed
during the strip). Running the text/plain stages there deletes real content:
`&gt;` decodes to `>` before the quote filter sees it, so a line of prose
opening with a chevron is dropped whole, and `2*3=6` becomes `23=6`. The
default, `"text/plain"`, is the thorough path — an unrecognised type
(`text/markdown`, an importer blob) is still fully cleaned, so you opt *into*
the literal reading rather than out of the cleaning. `parse_email` passes each
part's own type automatically.

The result is **plain text, not HTML** — it may contain `<`, `>`, `&` (e.g.
from `x < 5 & y > 3`). Escape it before rendering inside an HTML document.

Cleaning is deliberately **structural and language-neutral**: quoted-reply
history is dropped via `>` lines and `<blockquote>` only. Locale/product
heuristics — "view in browser" boilerplate, `On … wrote:` reply
attributions, forwarded-header blocks — are left to the application layer.

## Attachment filenames

`parse_email` reports every part `name` already sanitized.
`sanitize_filename` is that same pass, exposed for names that never went
through the parser — a client-supplied one, say:

```python
from jmap_email import sanitize_filename

name = sanitize_filename(raw, max_length=255) or "unnamed"
```

It NFKC-normalizes, strips path components in both separator dialects
(`..\..\boot.ini` → `boot.ini`), removes every invisible character —
controls, bidi overrides, zero-width joiners, the BOM, lone surrogates —
replaces the characters filesystems reject, drops surrounding dots and
whitespace, and truncates while keeping the extension. The result is
safe to join onto a directory: no separator, no `..`, no leading dot (so
`.gitignore` comes back as `gitignore`), and NFKC-stable so normalizing
it later can't reintroduce any of those.

It returns `None`, never `""`, when nothing usable survives, so the
failure case can't be mistaken for a name. Pass the `max_length` your
storage enforces; the 255 default is a convention, not a promise about
your column.

Two of those steps are not cosmetic. The bidi strip:
`annexe<U+202E>gpj.exe` renders as `annexe.exe.jpg` in most file pickers
— the user sees an image, the OS sees an executable, and it is the
standard attachment spoof. And normalizing *first*: `．．／etc／passwd`
is all fullwidth characters, so it passes any ASCII-based check
untouched, then folds to `../etc/passwd` as soon as something downstream
normalizes it. Sanitize-then-normalize is the bypass; normalize-then-
sanitize is not.

A part carrying **no** filename is a different question. Per RFC 8621
`name` is `String | null`, and `parse_email` reports `null` rather than
inventing a placeholder. What to show instead — `unnamed`, a
subject-derived name, an extension inferred from the part's MIME type —
is product policy, and stays in your application.

## Validators

Want to know if a string would be accepted by `compose_email` as a
Message-ID without actually trying to compose? Use `is_valid_msg_id`:

```python
from jmap_email import is_valid_msg_id

if is_valid_msg_id(parent_header):
    reply["inReplyTo"] = [parent_header]
```

`is_valid_addr_spec` is its counterpart for addresses: `True` means one
well-formed mailbox — `local@domain`, no embedded whitespace, no comma
or semicolon splitting it in two, both halves non-empty — safe to put in
a header as it stands. A quoted-string local-part
(`"john doe"@example.com`) is accepted, since the quoting is what keeps
it a single mailbox. The parser and the composer share this predicate,
so a value one side calls an address is never one the other would mangle.

It checks the shape a mailbox-list entry requires — one bare
`local@domain` addr-spec, nothing that would split it into two
mailboxes or need quoting on the way out — but returns `True`/`False`
instead of raising. Useful for lenient parse paths (archive importers,
inbound salvaging) that need to decide between keeping an address and
dropping it without catching an exception.

**`True` means the value is usable as it stands.** The predicate
rejects anything `compose_email` would refuse — a line terminator, a
control character, an unquoted special. Answering `True` for
`"a@b.co\r\n"` would be handing back a header-injection payload to
anyone who writes the value somewhere other than `compose_email`. The
composer enforces the same rule: a malformed `email` value raises
`InvalidAddressError` rather than being silently cleaned, so the
predicate and the composer agree — `True` here is exactly the set of
values the composer emits unchanged.

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

### Formatting addresses for display

`format_address(name, email)` and `format_address_list(addresses)` go the
other way — from parsed values to a display string for a human, not a
header for the wire.

```python
from jmap_email import format_address, format_address_list

format_address("Alice", "alice@example.com")      # "Alice <alice@example.com>"
format_address("Hara, Alice", "a@example.com")    # '"Hara, Alice" <a@example.com>'
format_address("", "alice@example.com")           # "alice@example.com"

format_address_list(parsed["to"])                 # "Alice <a@x.co>, b@x.co"
```

A display name is quoted only when it needs to be, and an entry with no
name reduces to the bare addr-spec, so the output reads the way a mail
client shows it.

`compose_email` already formats addresses for the messages it builds, so
these are not needed on the send path. They exist for text you write
*around* a message: the `From:`/`To:`/`Cc:` block of a forwarded-message
attribution, a "replying to" line, an audit log entry.

`format_address_list` takes an `EmailAddress[]` — `parsed["to"]`,
`parsed["cc"]`, … — and returns `""` for an empty list. It does **not**
accept `None`, which is what those fields hold when the header is absent,
so guard with `parsed["cc"] or []`.

## Defense matrix

The parser explicitly defends against the documented attack classes
below. See the `tests/` directory for regression coverage of each.

- **CVE-2023-27043** — `parseaddr`/`getaddresses` display-name confusion,
  in both its forms: the multi-tuple split (`"a@b.co" <real@you.com>`,
  where the authoritative angle-addr is taken rather than the first
  tuple) and the unclosed-comment variant
  (`victim@bank.com( <attacker@evil.co>`, where the comment eats the
  angle-addr and leaves only the display-name-as-addr-spec — refused)
- **CVE-2019-16056** — multiple-`@` addr-spec: an allowlist keyed on the
  domain talked into accepting one it meant to deny
- **CVE-2023-36632** — `parseaddr` `RecursionError` on nested comments
- **CVE-2026-30227** (MimeKit) — CR/LF in a quoted-string local-part;
  the same shape reached this library as an addr-spec carrying a space
  or a comma, which is two mailboxes rather than one
- **CVE-2023-51764** — SMTP smuggling. Some receivers accept `\n.\n` as
  a DATA terminator, so a composer that emitted a bare LF would mint the
  vector out of body text; our output is strictly CRLF, asserted
- **CVE-2025-52488** — Unicode normalization bypass: compatibility forms
  that survive an ASCII check and fold to `../` afterwards
  (`sanitize_filename` normalizes *before* it sanitizes)
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
- **CVE-2026-1299** — `BytesGenerator` header injection via unquoted
  newlines. Fixed in CPython 3.14.3, below this package's 3.14.6 floor,
  so the floor is what defends it — not our code

## Compatibility

- **Python** 3.14.6+ (see [Why a Python 3.14.6 floor?](#why-a-python-3146-floor))
- **Platforms tested in CI:** Linux on x86_64 and arm64
- **macOS / Windows / PyPy / free-threaded build:** untested; expected
  to work since the package has zero compiled extensions and a single
  pure-Python runtime dependency (`idna`). Reports of breakage welcome
  via the issue tracker.

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

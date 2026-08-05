# Changelog

All notable changes to `jmap-email` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-08-05

### Changed

- **Breaking:** the package is no longer dependency-free: IDNA encoding
  (`ComposeOptions(idna_encode_domains=True)`) now uses the
  [`idna`](https://pypi.org/project/idna/) package (UTS 46,
  non-transitional) instead of the stdlib IDNA2003 codec. The accepted
  range is `idna>=3.7,<4` — the floor is the CVE-2024-3651 fix, the cap
  keeps a future UTS 46 revision from changing what a domain encodes to
  without a release here. Deviation code points are no longer
  folded — `faß.de` encodes to `xn--fa-hia.de` instead of silently
  becoming the *distinct* registrable domain `fass.de` (likewise the
  Greek final sigma) — and labels IDNA2008 disallows (emoji) are now
  refused. Empty labels, over-long labels, and a trailing root dot are
  refused as before.

- **Breaking:** `parse_email` returns `None` when any header field
  exceeds `max_header_value_bytes` (previously the value was truncated
  and the message parsed).
- `decode_rfc2047_header` bounds its own input at
  `max_header_value_bytes`: the stdlib's `decode_header` is O(n²) in the
  number of encoded-words, and attachment filenames reach it untruncated.
- The unexported `MAX_*` mirror constants on `jmap_email.parser` are
  removed; read the fields on `DEFAULT_PARSE_OPTIONS` instead.
- **Breaking:** `compose_email`'s `keep_bcc` argument moved into a
  `ComposeOptions` bundle as `emit_bcc`, for symmetry with `ParseOptions`:
  `compose_email(jmap, options=ComposeOptions(emit_bcc=True))`.
  `in_reply_to` and `prepend_headers` stay keyword arguments.
- **Breaking:** `compose_email`'s `allow_extensions` argument is removed;
  `_ext` is always accepted. The composer never read it, so the flag could
  reject input but never change output.
- `msgid_chain` drops entries it cannot write into a header (line
  terminator, internal whitespace, nested angle bracket) instead of
  emitting them. Well-formed ids, including `<foo$@local@domain>` and
  `<12345>`, are unaffected.
- `sent_at_to_datetime` always returns a tz-aware datetime; naive input is
  stamped UTC. A naive return made comparison with an aware datetime raise.
- `preview_text` no longer lets a line-anchored markdown construct span
  lines: `"alpha\n-\n-\n-\nbravo"` was previewing as `alpha bravo`,
  now `alpha - - - bravo`.
- The shape accessors accept only a `list`; a tuple or generator now
  returns the empty default.

### Added

- `ComposeOptions` / `DEFAULT_COMPOSE_OPTIONS`, the compose-side peer of
  `ParseOptions`: frozen, hashable, `dataclasses.replace`-able.
  - `idna_encode_domains` (default `False`) — IDNA-encodes a non-ASCII
    **domain** to its A-label (`contact@exemplé.fr` →
    `contact@xn--exempl-gva.fr`), the only form 7-bit SMTP and the MX
    lookup carry. Nothing in the stdlib does this conversion.
  - `allow_8bit` (default `False`) — emits non-ASCII bodies as 8-bit.
    Needs 8BITMIME (RFC 6152) on the hop.
  - `allow_smtputf8` (default `False`) — emits UTF-8 headers (RFC 6532)
    and permits a non-ASCII local part. Needs SMTPUTF8 (RFC 6531) on the
    hop. Implies `allow_8bit`.

  The `allow_*` pair names hop capabilities this library does not
  discover; the defaults assume nothing and emit pure-ASCII 7-bit. They
  are permissions, not instructions — an all-ASCII message composes
  byte-identically either way. See the README for the
  compose-both-and-fall-back pattern.
- `sanitize_filename` is now public, for names that never went through
  `parse_email`. Returns `None`, never `""`. A nameless part still reports
  `name: null`; synthesizing a placeholder stays consumer policy.
- `is_valid_addr_spec` is now public and shared by the parser and the
  composer: `True` means one well-formed mailbox, usable as it stands.
- Sixteen `_ext.defects` markers for MIME constructs different parsers
  resolve differently, from the *Inbox Invasion* (CCS '24) and *Email
  Smuggling* (2025) evasion catalogues. See the README table.
  `message/rfc822` is deliberately unmarked — it fires on every forward.

### Fixed

- **Remote DoS: deeply nested MIME.** `BufferedSubFile.readline` tests
  every body line against every ancestor predicate, making the stdlib
  parse O(depth × lines). `_FastSubFile` skips that scan for lines that
  cannot be a delimiter: cost flat in depth, identical output.
- **Sender forgery via header truncation.** Header values were cut at a
  byte bound and *then* parsed, so a padded `From` could be made to
  parse to an address nobody sent — which became the stored sender and
  the DKIM alignment domain. Over-long fields are now rejected; address
  lists cut back to a top-level separator and record
  `AddressListTruncatedDefect`.
- **A display name could become a recipient**, two ways: the quoting check
  treated a lone `"` as an already-quoted name, and RFC 2047 decoding
  happened after the quoting decision, so `=?utf-8?B?ZXZpbEB4LmNv?=`
  went out unquoted and decoded to a second mailbox.
- **An unclosed comment let a display name become the parsed sender.**
  `From: victim@bank.com( <attacker@evil.co>` reported `victim@bank.com`.
  CVE-2023-27043 by another route; such headers are now refused.
- **Addr-spec validation was too loose.** A comma made one value two
  recipients; RFC 5322 specials (`( ) [ ] : \`) unquoted changed the
  recipient count or mutated the address; an unterminated quoted-string
  local-part swallowed the next recipient; a control character was
  silently cleaned and a different address emitted.
- **Non-ASCII addr-specs were silently mangled** into encoded-words that
  RFC 2047 §5 forbids. Now converted under `idna_encode_domains` /
  `allow_smtputf8`, or refused.
- **MIME boundaries now come from a CSPRNG**, not Mersenne Twister.
- **Quadratic matching reachable from attacker input.** The angle-addr
  check ran `<[^<>]*@[^<>]*>`, where both halves can match `@`, so a `<`
  followed by a run of `@` made the engine try every split point — one
  96 KiB message cost ~43s of CPU in `parse_email`. The markdown link and
  autolink patterns had the same shape: at `max_chars=65536` a body of
  repeated `[` or `<` cost 65s and 114s in `preview_text`. All are now
  anchored or bounded, and linear.
- **Malformed shapes raised `AttributeError` before being wrapped.**
  `format_address_list` and the attachment partition called `.get` on
  entries that need not be dicts, so `{"to": "x"}` logged a full traceback
  on every call — a log-flooding vector. Address entries that are not
  dicts are skipped; a non-dict attachment raises `AttachmentError`,
  since dropping one is invisible data loss.
- **The null-safe accessors could raise** on a truthy non-iterable
  `headers` or a non-string header name.
- `parse_email` threads `options=` into address parsing, so
  `max_address_list_bytes` applies on the entry point that meets hostile
  mail; and defects are harvested after body parsing, so decode-time
  defects are no longer dropped.
- RFC 6266 §4.3: `filename*` now wins over `filename`.

### Internal

- Four new Hypothesis suites under `pytest -m fuzz` (parse/compose seam,
  wire round-trip, filenames, helpers) plus `test_ambiguity_defects`.
  Fuzz phases are Hypothesis's defaults now, so failures shrink and
  replay instead of vanishing.
- `preview_text` gained wall-clock complexity guards: the line-anchored
  patterns were quadratic in the head, whose size scales with `max_chars`.

## [0.2.0] - 2026-07-22

### Added

- `ParseOptions.max_preview_chars` (default 256, the RFC 8621 §4.1.4
  ceiling) caps the length of the server-set `preview` excerpt. Lower it
  via `options=` to produce a shorter preview (e.g. a list-view snippet).
- `ParseOptions.max_preview_scan_bytes` (default 128 KiB) bounds the body
  text scanned for the `preview`, so a body that is almost entirely markup
  (which never reaches the text budget) can't run the extractor away.
- `preview_text` helper (also used internally for the `preview` field):
  turns HTML/markdown body text into a single, display-ready line. It strips
  HTML (dropping `<script>`/`<style>`/`<title>`/`<blockquote>` payloads and
  unwrapping markdown autolinks), drops `>`-quoted text/plain lines, strips
  markdown
  (incl. ATX + setext headers), collapses whitespace, and removes control
  characters, ANSI/terminal escape sequences, and invisible format-character
  "preheader spacers" (soft hyphen, ZWSP/ZWNJ, word joiner, BOM).
- `preview_text(..., content_type=...)` selects the cleaning stages per wire
  format. The `>`-quoted-line drop and the markdown strip are text/plain
  conventions, so they are skipped for `text/html`, where `>`, `*` and `_`
  are literal characters the sender meant to be shown and quoted history
  arrives as `<blockquote>` (already suppressed). Without this, `&gt;`
  decodes to `>` before the quote filter and a chevron line of an HTML body
  is deleted whole, while `2*3=6` becomes `23=6`. The HTML strip and the
  control-character hardening run for every type. Defaults to `"text/plain"`,
  the thorough path, so an unrecognised type is still fully cleaned;
  `parse_email` passes each part's own type.

### Changed

- **BREAKING:** the RFC 8621 `TypedDict` shapes (`JmapEmail`, `EmailAddress`,
  `EmailBodyPart`, `EmailBodyValue`, `EmailHeader`, `Attachment`,
  `JmapEmailExt`) are no longer re-exported from the top-level package;
  import them from `jmap_email.types` instead.
- **BREAKING:** `ParseLimits` → `ParseOptions`, `DEFAULT_PARSE_LIMITS` →
  `DEFAULT_PARSE_OPTIONS`, and the `limits=` keyword on `parse_email` /
  `parse_addresses` → `options=`. The bundle now also carries format policy
  the RFC leaves to the server (preview length), not just resource caps.
  Renamed outright rather than aliased: the keyword only ever took a
  `ParseLimits`, which is gone in the same release, so a `limits=` shim would
  have accepted a call whose argument can no longer be constructed.
- `preview` (RFC 8621 §4.1.4) is now cleaned through `preview_text` to strip
  HTML and markdownish syntax. The HTML strip interrupts itself once it has
  collected a small multiple of the preview's worth of text, so the default
  `preview=True` parse stays cheap on large messages instead of scanning the
  whole body part. The multiple is what lets a body whose text carries heavy
  indentation or markdown still fill the preview to its cap: the cleaning
  stages only shrink the text, so collecting exactly the cap fell short
  (measured 79% of the budget on a pretty-printed newsletter, 50% on
  markdown).
- `preview` is now drawn from the HTML body when present (matching the part
  clients render), falling back to the plain-text body when the HTML yields
  no visible text (e.g. a link-/image-only part) rather than shipping blank.

## [0.1.0] - 2026-06-08

Initial release. Extracted from the
[Messages](https://github.com/suitenumerique/messages) project.

[0.3.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.3.0
[0.2.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.2.0
[0.1.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.1.0

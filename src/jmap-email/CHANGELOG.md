# Changelog

All notable changes to `jmap-email` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-08-01

### Changed

- **Breaking:** `compose_email`'s `keep_bcc` keyword argument is now
  `ComposeOptions.emit_bcc`, passed via `options=` for symmetry with
  `parse_email(..., options=ParseOptions(...))`. `in_reply_to` and
  `prepend_headers` stay keyword arguments — they are per-message data,
  not call-site policy.

  ```python
  compose_email(jmap, keep_bcc=True)                              # before
  compose_email(jmap, options=ComposeOptions(emit_bcc=True))      # after
  ```

- **Breaking:** `compose_email`'s `allow_extensions` argument is removed;
  an `_ext` key is now always accepted. The composer never read `_ext`,
  so the flag could reject input but never change output. Check
  `"_ext" in data` yourself if you need that assertion.

### Added

- `ComposeOptions` / `DEFAULT_COMPOSE_OPTIONS` — the compose-side peer of
  `ParseOptions`: frozen, hashable, `dataclasses.replace`-able.
- `ComposeOptions.idna_encode_domains` (default `False`) — IDNA-encodes a
  non-ASCII **domain** to its A-label form (`contact@exemplé.fr` →
  `contact@xn--exempl-gva.fr`), the only form 7-bit SMTP and the MX
  lookup can carry. Domain only: a non-ASCII local part is not a DNS
  label and has no A-label, so it needs `allow_smtputf8`. Nothing in the
  stdlib does this conversion — `email.policy` RFC 2047-encodes the
  domain instead, which RFC 2047 §5 forbids inside an addr-spec.
- `ComposeOptions.allow_8bit` (default `False`) — emits non-ASCII bodies
  as raw 8-bit rather than base64/QP. Needs 8BITMIME (RFC 6152) on the
  hop.
- `ComposeOptions.allow_smtputf8` (default `False`) — emits UTF-8 headers
  (RFC 6532) and permits a non-ASCII local part. Needs SMTPUTF8 (RFC
  6531) on the hop plus the `SMTPUTF8` parameter on `MAIL FROM`. Implies
  `allow_8bit`.

  Both `allow_*` flags name capabilities of the hop, which this library
  does not discover; the defaults assume nothing and emit pure-ASCII
  7-bit. They are permissions, not instructions — an all-ASCII message
  composes byte-identically either way. See the README for the
  compose-both-and-fall-back pattern, and note it only works when the
  *domain* is non-ASCII: RFC 6530 provides no downgrade for a UTF-8
  mailbox name.
- `sanitize_filename` is now public (previously the parser-internal
  `_sanitize_filename`), for names that never went through `parse_email`
  — a client-supplied one, say. Pass `max_length=` to match your storage;
  the default stays 255. Returns `None`, never `""`, matching the
  `String | null` shape of the RFC 8621 field it feeds. A nameless part
  still reports `name: null`; synthesizing a placeholder, extension
  included, stays consumer policy.
- `is_valid_addr_spec` is now public and shared by the parser and the
  composer: `True` means one well-formed mailbox, safe to place in a
  header as it stands.
- Sixteen `_ext.defects` markers for MIME constructs that different
  parsers legitimately resolve differently. We resolve each the way the
  stdlib does and parse on; the marker lets a scanning or quarantine
  policy see that a choice was made. Motivated by *Email Smuggling with
  Differential Fuzzing of MIME Parsers* (Andarzian, Meyers & Poll, 2025)
  and *Inbox Invasion* (CCS '24), which smuggle payloads past filters on
  exactly these. See the README table for what each one means.

  `DuplicateFromDefect`, `DuplicateScalarHeaderDefect`,
  `DuplicateContentTypeDefect`, `DuplicateTransferEncodingDefect`,
  `UnrecognizedTransferEncodingDefect`, `DuplicateBoundaryParameterDefect`,
  `MissingMimeVersionDefect`, `NonEmptyPreambleDefect`,
  `NonEmptyEpilogueDefect`, `ConflictingAttachmentNameDefect`,
  `ControlCharInHeaderDefect`, `EmptyBoundaryDefect`,
  `EncodedWordInParameterDefect`, `FoldInQuotedParameterDefect`,
  `PartialMessageDefect`, `ExternalBodyDefect`.

  The last three come from the *Inbox Invasion* evasion catalogue and the
  nested-RFC822 technique seen in the wild: a fold *inside* a quoted
  `filename` gives one attachment three names depending on who unfolds
  it, while `message/partial` and `message/external-body` put the payload
  somewhere a per-message content scanner never looks. `message/rfc822`
  is deliberately not marked — it fires on every forward, so it would be
  noise; re-parse the attachment's `content` to look inside.

### Fixed

- **An unclosed comment let a display name become the parsed sender.**
  `getaddresses(strict=False)` treats an unclosed `(` as a comment
  running to end of input, so in
  `From: victim@bank.com( <attacker@evil.co>` the comment ate the
  angle-addr and the splitter returned one tuple whose *address* was the
  text the sender typed in display-name position — `parse_email` reported
  `victim@bank.com` as the sender of a message from `attacker@evil.co`.
  CVE-2023-27043 by another route: `_pick_best_address` defends the
  multi-tuple form by preferring the last plausible tuple, but here the
  bogus tuple is the only one. Such a header is now refused by
  `parse_address` / `parse_addresses`, matching the stdlib strict parser.
  A quoted display name legitimately containing an angle-addr
  (`"Bob <bob@old.co>" <bob@new.co>`) and ordinary balanced comments are
  unaffected. Found by the address fuzz suite.
- **A display name could become a recipient, via the quoting check.**
  `format_address` skipped re-quoting a name that "looked quoted", tested
  as `startswith('"') and endswith('"')` — which a lone `"` satisfies at
  once, as do `"a"b"` and `"a\"`. Emitted verbatim, the unbalanced quote
  unbalanced the header, and in a mailbox-list the *next* entry's display
  name was then read as an address: `To: [{"name": '"', ...},
  {"name": ", evil@x.co", ...}]` re-parsed to `evil@x.co` with both real
  recipients gone. Same root cause as the unterminated local-part below;
  both now go through one helper. Found by the round-trip fuzz property.
- **An attacker-chosen display name could become a recipient.** Quoting
  was decided by inspecting the string handed in, but the header
  machinery decodes RFC 2047 encoded-words afterwards — so a name of
  `=?utf-8?B?ZXZpbEB4LmNv?=` went out unquoted and decoded to
  `To: evil@x.co <a@b.co>`, two mailboxes where the caller supplied one.
- **A single `email` value containing a comma became two recipients.**
  `format_address` sanitized injection characters but never checked
  addr-spec shape, and mailbox-lists are built by joining on commas.
  `compose_email` now raises `InvalidAddressError`; `format_address`
  returns `""`.
- **An unterminated quoted-string local-part was accepted.**
  `is_valid_addr_spec` deleted `\\` and `\"` pairs then looked for a
  leftover quote, which passes a local-part ending in a lone backslash —
  in `"a\"` it escapes the closing `"`. Such a value keeps quoting past
  its own end and swallows the comma before the next recipient.
  Quoted-pairs are now walked left to right.
- **Non-ASCII addr-specs were silently mangled.** `é@ü.co` went out as
  `=?utf-8?q?=C3=A9?=@=?utf-8?q?=C3=BC?=.co` — an encoded-word inside an
  addr-spec, which RFC 2047 §5 forbids and no MTA routes. Now converted
  under `idna_encode_domains` / `allow_smtputf8`, or refused.
- **An address carrying a control character was silently cleaned and
  emitted.** The composer validated the *sanitized* value, so
  `references@1&Q\x1a` had the control character deleted, passed the
  shape check, and went out as `references@1&Q` — a recipient the caller
  never supplied. Addresses are now checked as supplied (surrounding
  whitespace aside) and `is_valid_addr_spec` rejects anything the
  composer would strip, matching the rule `is_valid_msg_id` already
  followed. Found by the round-trip fuzz property.
- **MIME boundaries now come from a CSPRNG.** The stdlib generates them
  with `random.randrange` — a Mersenne Twister, whose state is
  recoverable from enough consecutive outputs, letting a party who can
  collect boundaries predict the next one and inject MIME parts.
- **The null-safe accessors could raise.** `find_header`, `find_headers`
  and `has_header` read `parsed_email.get("headers") or []`, where a
  truthy non-iterable (`5`, `True`) raises `TypeError`, and called
  `.lower()` on `entry["name"]` without checking it was a string. Found
  by the `test_helpers_fuzz` property.
- `parse_email` now threads `options=` into address-header parsing, so
  `max_address_list_bytes` (the CVE-2024-23184 analogue) actually applies
  on the entry point that meets hostile mail.
- Defects are now harvested after body parsing, so the stdlib's
  decode-time defects (`InvalidBase64CharactersDefect` and siblings) are
  no longer dropped.
- RFC 6266 §4.3: `filename*` now wins over `filename`, so a sender can no
  longer show us `safe.txt` and every spec-following client `evil.exe`.

### Internal

- Four new Hypothesis suites, all under `pytest -m fuzz`:
  `test_roundtrip_fuzz` (5 properties on the parse→compose seam:
  containment of recipients, headers, MIME parts and addresses),
  `test_wire_roundtrip_fuzz` (5, on raw wire bytes), `test_filenames_fuzz`
  (6, on `sanitize_filename`) and `test_helpers_fuzz` (6, on the
  never-raises guarantee). Plus `test_ambiguity_defects` (22) for the new
  markers, and a mailbox-count property in `test_address_fuzz`.
- `test_preview.py` gained wall-clock complexity guards: every
  line-anchored markdown pattern used `\s` for its leading run, which
  under `re.MULTILINE` rescans the whole whitespace run from every line
  start — quadratic in the head, whose size scales with `max_chars`.

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

[ietf-mime]: https://datatracker.ietf.org/doc/draft-chen-email-mime-ambiguity-defense/
[0.3.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.3.0
[0.2.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.2.0
[0.1.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.1.0

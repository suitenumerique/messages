# Changelog

All notable changes to `jmap-email` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-29

### Added

- Public attachment-filename helpers, extracted so consumers can apply
  the same mechanics to names that never went through `parse_email`
  (e.g. client-supplied names):
  - `sanitize_filename` (previously the parser-internal
    `_sanitize_filename`): path/control-character stripping and
    extension-preserving truncation to 255 chars.
  - `guess_mime_extension`: MIME type → file extension, correcting the
    stdlib `mimetypes` table where mail-borne reality disagrees
    (`text/calendar` → `.ics`, `application/xml` → `.xml` not `.xsl`,
    `application/octet-stream` → no extension, Outlook's
    `application/x-zip-compressed` → `.zip`, …). Accepts a full
    `Content-Type` header value; parameters are dropped before lookup.

  Neither is applied by `parse_email` beyond the existing sanitization:
  a nameless part still reports `name: null` per RFC 8621 —
  synthesizing a placeholder remains the consumer's decision.

### Removed

- Dead private helper `_build_attachment_dict`

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

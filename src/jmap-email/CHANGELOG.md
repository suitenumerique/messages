# Changelog

All notable changes to `jmap-email` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

Initial release. Extracted from the [Messages](https://github.com/suitenumerique/messages)
project after a four-round security hardening pass.

### Added

- `parse_email(raw_bytes)` — lenient RFC 5322 / MIME parser producing
  a strict-JMAP Email object (RFC 8621 §4) subset.
- `compose_email(email)` — strict-by-design RFC 5322 composer accepting
  the JMAP Email shape on input.
- `make_reply(original, …)` / `make_forward(original, …)` — JMAP Email
  template builders for replies and forwards.
- Field-level helpers: `parse_address`, `parse_addresses`, `parse_date`,
  `decode_header`, `format_address`, `format_address_list`,
  `reply_subject`.
- HTML utilities: `extract_inline_images_html`, `extract_inline_images_text`,
  `remove_mime_headers`.
- Opt-in JMAP conformance flags: `body_values`, `body_structure`,
  `preview` (off by default for performance).
- Project-extension sub-dict `ext` with `defects`, `gmailLabels`,
  `headersBlocks` (on by default; `include_extensions=False` to omit).
- Resource limits: `_MAX_MIME_NESTING_DEPTH=100`, `_MAX_MIME_PARTS=1000`,
  `_MAX_HEADER_VALUE_BYTES=102_400`, `_MAX_ADDRESS_LIST_BYTES=100_000`.
- Defense layers documented against CVE-2023-27043, CVE-2024-6923,
  CVE-2024-21742, CVE-2024-23184, PortSwigger "Splitting the Email Atom"
  (DEF CON 32 2024), gh-114906, gh-136063, gh-137687.

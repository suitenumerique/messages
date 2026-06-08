# Changelog

All notable changes to `jmap-email` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-08

Initial release. Extracted from the
[Messages](https://github.com/suitenumerique/messages) project.

### Added

- `parse_email(raw_bytes)` — lenient RFC 5322 / MIME parser producing
  a strict-JMAP Email object (RFC 8621 §4).
- `parse_headers(raw_bytes)` — same shape as `parse_email` but skips
  the body walk; useful for header-only indexing.
- `compose_email(email)` — strict-by-design RFC 5322 composer accepting
  the JMAP Email shape on input.
- `make_reply(original, …)` / `make_forward(original, …)` — JMAP Email
  template builders for replies and forwards.
- Field-level parsers: `parse_address`, `parse_addresses`,
  `parse_date`, `decode_rfc2047_header`.
- Formatters: `format_address`, `format_address_list`, `reply_subject`.
- Null-safe shape accessors: `first_address`, `first_address_email`,
  `first_address_name`, `first_msgid`, `msgid_chain`,
  `sent_at_to_datetime`, `find_header`, `find_headers`, `has_header`,
  `body_part_text`, `body_text_joined`.
- RFC 8621 Resent-* typed projections: `resentFrom`, `resentSender`,
  `resentReplyTo`, `resentTo`, `resentCc`, `resentBcc`,
  `resentMessageId`, `resentDate`.
- Spec-default emission of `preview`, `bodyValues`, and full per-part
  shape (`partId`, `blobId`, `size`, `headers`, `name`, `type`,
  `charset`, `disposition`, `cid`, `language`, `location`, `subParts`)
  on every `EmailBodyPart`.
- Composer error hierarchy: `ComposeError` base + `InvalidAddressError`,
  `InvalidMessageIdError`, `InvalidDateError`, `AttachmentError`,
  `HeaderInjectionError`.
- Project-extension sub-dict `ext` with `defects` (parser-collected
  stdlib `MessageDefect` class names).
- Resource caps as a per-call `ParseLimits` frozen dataclass passed
  via `parse_email(..., limits=...)` / `parse_addresses(..., limits=...)`.
  Defaults: `max_mime_nesting_depth=100`, `max_mime_parts=1000`,
  `max_header_value_bytes=102_400`, `max_address_list_bytes=100_000`.
  Module-level `MAX_*` constants on `jmap_email.parser` are kept as
  read-only documentation of the defaults; they are not a runtime
  tuning hook.
- Defense layers documented against CVE-2023-27043, CVE-2024-6923,
  CVE-2024-21742, CVE-2024-23184, CVE-2002-1337, CVE-2002-2325,
  Mailsploit, Inbox Invasion (CCS '24), PortSwigger "Splitting the
  Email Atom" (DEF CON 32 2024), gh-114906, gh-136063, gh-137687, and
  the USENIX 2020 "Weak Links in Authentication Chains" findings.

[0.1.0]: https://github.com/suitenumerique/messages/releases/tag/jmap-email-0.1.0

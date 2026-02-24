# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-02-24

### Added

- Add configurable help center button in header #537
- Add outbound message recipients throttling #506
- Add webhook and logging for selfchecks, replacing pushgateway #550
- Add mailbox export in mbox format with labels #553
- Add PST import support and streaming for mbox #544
- Add denylist for personal mailbox prefixes #540
- Add multi-column layout block for signature editor #551
- Add celery task events for worker monitoring #549
- Add image block in template, signature and message composers
- Add storage usage metrics API endpoint #538
- Add conditional outbox folder
- Add stronger DNS checks with configurable records #522
- Add print button in messages context menu #518
- Add autofocus option to message, template and signature composers
- Add arm64 platform support for Docker image builds #554

### Changed

- Replace queue-based save/send orchestration with async promise ref
- Use display_name for labels and auto-unfold active parents #547
- Optimize MessageTemplate serialization and body handling #545
- Defer HTML/text body export to send/save time
- Add composer tools (text color, side menu and drag block handle)
- Improve outbox wording #539
- Replace nginx with Caddy for frontend reverse proxy and Scalingo deployment #556
- Replace MinIO with RustFS for object storage in development #556
- Migrate Python packaging from Poetry to uv #556
- Standardize and rename Makefile targets #556
- Upgrade Python to 3.14 #556
- Remove Django i18n and backend translation catalogs #556

### Fixed

- Delete orphan attachments when removed from draft #532
- Fix cursor position when clicking in combobox input #534
- Close left panel when clicking active folder on mobile
- Close thread after send only if needed

### Security

- Prevent IDOR on ThreadAccess thread and mailbox fields #557
- Add defense in-depth for XSS vulnerabilities #520

## [0.2.0] - 2026-02-03

### Added

- Display calendar invites in messages #481
- Add integrations view in mailbox settings #488
- Allow to retry send Message in Django Admin and filter Message by delivery status #499
- When forwarding a message, the attachments are added to the draft as new attachments #485
- Add InboundMessage admin view #505
- Add `worker.py` command and improve task routing on queues #504

### Changed

- Add loading state to the refresh button #511
- Refactor permissions code for viewsets #503

### Fixed

- Strip NUL bytes from email content #524
- Raise new "DUPLICATE" error when there are 2 SPF records #521
- Fix memory leak with large mbox file import #516
- Fix env var still overriding the Celery default
- Add default "invitation.ics" name for invite downloads
- Make celery app name explicit to fix potential $APP override
- Fix a few edge cases in email parser #507
- Fix duplicate recipient creation errors #496
- Fix SSL error and improve authentication failure #495

## [0.1.1] - 2026-01-22

### Fixed

- Now `DJANGO_ADMIN_URL` must not end with `/`.

## [0.1.0] - 2026-01-20

### Added

- Allow to save an attachment into Drive workspace #408
- Add a SPAM folder in mailbox panel
- Allow to search for spam messages
- Add `is_trashed` flag to thread model
- Add to select multiple threads in thread panel
- Add image proxy endpoint to display external images in messages
- Add `to_exact` modifier to search query
- Allow to toggle spam status of a thread

### Changed

- Configure Drive App Name through environment variable (DRIVE_APP_NAME)
- Inherit OIDC Authentication backend from django-lasuite #408
- Exclude `is_trashed` and `is_spam` threads from search results by default
- `to` search modifier now looks for messages where recipient fields (to, cc, bcc) contain the given email address.

[unreleased]: https://github.com/suitenumerique/messages/compare/v0.3.0...main
[0.3.0]: https://github.com/suitenumerique/messages/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/suitenumerique/messages/releases/v0.2.0
[0.1.1]: https://github.com/suitenumerique/messages/releases/v0.1.1
[0.1.0]: https://github.com/suitenumerique/messages/releases/v0.1.0

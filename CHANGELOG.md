# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Bump keycloak to 26.5.3 #543

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

[unreleased]: https://github.com/suitenumerique/messages/compare/v0.2.0...main
[0.2.0]: https://github.com/suitenumerique/messages/releases/v0.2.0
[0.1.1]: https://github.com/suitenumerique/messages/releases/v0.1.1
[0.1.0]: https://github.com/suitenumerique/messages/releases/v0.1.0

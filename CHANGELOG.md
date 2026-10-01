# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Bump keycloak to 26.8.0 and refresh the Alpine 3.24 digest in the Keycloak image

## [0.10.0] - 2026-09-29

### Added

- Compose messages in floating windows: several drafts side by side,
  minimized or full-screen, restored after a reload and synced across tabs
- Mobile UI for the native apps: bottom navigation bar, swipe actions on
  threads, pull-to-refresh, drawer, formatting toolbar above the keyboard,
  haptics
- Publish mobile OTA bundles from Scalingo deploys
- Persist the last active mailbox and select it on the next visit
- Clean snippet of the latest message in the thread list
  (`FEATURE_THREAD_SNIPPET`, after `backfill_thread_snippets`) and on folded
  messages
- Allow domain admins to export mailboxes #789
- Provisioning endpoint listing the DNS records of all domains #780
- ARC relay trust: `arc` inbound_auth mode and `arc_verdict` spam rule #763
- Support internationalized email addresses (IDNA domains, ASCII-folded
  local parts) #785
- Keep Gmail labels, Thunderbird read/starred flags and
  sent/archived/spam/trashed states through MBOX export and import
- Setup guides for the authentication provider and the identity provider
- IP allowlist for the Django admin URL in the frontend Caddy proxy
- Smoke test for the frontend production image (make test-front-distroless)
- IP allowlist for the Keycloak admin console and master realm in the Keycloak image (Caddy) #793
- Smoke test for the Keycloak production image (make test-keycloak-image) #793

### Changed

- Migrate the frontend from Cunningham to the `@gouvfr-lasuite/ui-components`
  and `@gouvfr-lasuite/ui-tokens` packages
- Upgrade Capacitor to 8.5 and adopt the iOS UIScene life cycle
- Upgrade jmap-email to 0.3.0: an unparseable inbound message is abandoned
  instead of retried, a compose error on send returns a 400, a stored message
  the parser now refuses is flagged unreadable
- Force-lowercase mailbox addresses #785
- Route mailbox export tasks to the imports queue #805
- Redirect to the inbox when switching mailbox
- Thread a reply by its In-Reply-To even when its subject was rewritten #765
- Exclude spam and trashed messages from folder stats by default
- Stop fetching stats for the Sent folder
- Page titles follow "Mailbox - Folder - App name"
- Harden pymta with new settings and limits, and improve its env vars,
  documentation and logging #777 #783
- Bump keycloak to 26.7.4 and Alpine to 3.24 in the Keycloak image #776 #784 #798
- Bump Caddy to 2.11.4 and lprobe to v0.2.0 in the frontend image
- Keycloak image: Keycloak listens on 127.0.0.1:8081, Caddy serves port 8080
- Keycloak image: proxy headers are fixed to xforwarded from Caddy, which sends X-Forwarded-Proto https

### Removed

- The mta-out service: relay mode now needs an external `MTA_OUT_RELAY_HOST` #785
- Scalingo buildpack for Keycloak (src/keycloak/buildpack, Procfile, system.properties)

### Fixed

- Fix a draft update / send race that could re-send an already delivered
  email, and keep the autosave out of the send window
- Add the channel to messages sent through the submit API #794
- Accept more valid SPF records by implementing the full RFC #782
- Ignore whitespace in DKIM keys in the DNS check #778
- Select IMAP folders whose unquoted name carried extra spaces, and stop
  retrying unselectable folders
- Give nameless attachments the same name in the UI, downloads and drafts
- Keep only BlockNote-supported colors in pasted content, drop unsupported
  blocks and embed external images again
- Resize the mail iframe with a resize observer (Safari, width changes,
  lazy-loaded images)
- Stretch the thread sender on a narrow thread panel
- Fix file uploads outside a secure context (`crypto.randomUUID` fallback)
- Fallback for avatar colors on browsers without `color-mix()` support
- Mobile: send mutations through CapacitorHttp with an explicit Origin so
  an HTTPS backend's CSRF check accepts them
- Grant view-realm to the Keycloak service account, needed by the 2FA toggle
- Run make test-keycloak against a freshly imported Keycloak realm
- List only assignable users and current assignees in the quick assign picker

### Security

- Turn the direct access grant off on the Keycloak rest-api client
- Deny API actions that don't declare an access check by default
- Check mailbox access for mailbox_id on message list and thread search
- Rate-limit the image proxy, restrict it to ports 80 and 443 and mark its
  responses private
- SSRF-check per-domain relays (internal ones need SSRF_ALLOWED_HOSTS)
- Validate the MailDomain custom_settings schema
- Restrict IMAP import ports (MESSAGES_IMPORT_IMAP_ALLOWED_PORTS)
- Use only the Referer hostname in widget message subjects
- Scope the DNS check endpoint through the admin queryset
- Refuse MTA requests with a 401 instead of a 500 when `MDA_API_SECRET` is
  unset
- Scope the flag endpoint draft cascade to editable messages and suspend
  personal API keys of deactivated users #804
- Harden email parsing against hostile mail (forged sender, smuggled
  recipients, quadratic regexes) through jmap-email 0.3.0, and bound the
  reply-unquoting regexes

## [0.9.0] - 2026-07-22

### Added

- Push notification system for iOS, Android and Web
- Bootstrap Capacitor mobile apps (iOS/Android) sharing the existing SPA
- Self-hosted OTA update chain for mobile apps
- Mobile OIDC session handoff via a one-time token on an allowlisted deep link
- Inbound webhooks and message postmarks
- Host allowlist to bypass SSRF checks on internal networks
- Detect raw text links in HTML bodies and warn the user before redirect #744
- Support a `next` param at login to restore the requested route after auth
- Display unread count in the mailbox dropdown #738
- Add `X-Mailer` header on outbound messages
- Cache-busting source version in build
- Full documentation on spam processing

### Changed

- Rewrite MTA-in in pure Python to remove the Postfix dependency #692
- Refactor imports: retries, continuous mode, direct-to-offload storage,
  cancellation and a list UI in the settings modal #742
- Serve frontend configuration from the backend #734
- Deliver the CSRF token via the session instead of a cookie
- Generate message-id with the builtin `email.utils.make_msgid` #730
- Set up browserslist and Vite legacy plugin to support Chrome >= 109 #741 #750
- Reset search when switching mailbox #743
- Clearer message on the no-mailbox view
- Explicit feedback when a user is authenticated on the IdP but has no account
- Improve `make bootstrap` setup time and overall DevX
- Bump `django-lasuite` to 0.0.27
- Upgrade Keycloak theme to 2.3.4 #732

### Removed

- Remove the `TESTDOMAIN` feature, superseded by autojoin domains
- Remove the `react-email` component from outbound message rendering

### Fixed

- Fix relay block indentation breaking SASL auth in MTA out #733
- Fix Outlook Web handler in the unquote logic #754
- Fix premature line wrapping in the composer on Safari 26 #735 #740
- Fix line break in the composer on Chrome for Android #725
- Set an attachment name fallback
- Fix hardcoded `lang=en` that might trigger auto-translate
- Improve re-processing of inbound messages from the admin
- Save the origin IP across STARTTLS restarts (pymta)

### Security

- Bump keycloak to 26.6.4 (CERTFR-2026-AVI-0815) #729

## [0.8.0] - 2026-06-18

### Added

- Allow permanently deleting drafts and improve draft edition
- Allow passwordless mailbox creation when identity sync is off #707
- Gather mailbox settings into a dialog
- Translate template placeholder and add `user_name` builtin variable
- Report selfcheck status to Sentry crons #694

### Changed

- Drop Next.js for Vite + TanStack Router #675
- Move email parser & composer to new `jmap-email` library #700
- Add PyPI release scripts for `jmap-email`
- Use `LaGaufreV2` component
- Improve thread navigation a11y and multiselect UX #708
- Refine mailbox dropdown menu #705
- New homepage illustration #702
- Internationalize missing strings
- Wrap autoreply date column
- Bump `dompurify` to 3.4.11
- Bump `django-lasuite` to 0.0.26 #689

### Fixed

- Fix composer issues
- Add `To` header to outbound mails missing one #712
- Manage message/delivery-status attachments at compose
- Persist mailbox name when contact is missing
- Fix order and default calendar selection when RSVPing #699
- Fix display of recurring events with exceptions #686
- Fix opportunistic TLS against MXes with mismatched certs #687
- Fix mbox detection as `text/html` with some libmagic versions #696
- Complete PST email folder prefixes list
- Fix milter socket permission race on startup #693

### Security

- Add some defense-in-depth bits #706
- Harden SMTP connection & proxies config
- Harden inbound email parsing #695

## [0.7.0] - 2026-05-28

### Added

- Attachments preview #676
- Add link to a CalDAV instance to accept events directly #584

### Changed

- Improve sending experience #681
- Remove deprecated model fields from tiered storage migration #678

### Fixed

- Unmount thread view immediately on unselect thread #680
- Prevent refetch thread messages on draft deletion #682

## [0.6.0] - 2026-05-20

### Added

- Add thread assignation feature #645
- Add mention notifications via UserEvent #621
- Allow sending internal messages through ThreadEvent #566
- Add thread deep linking #664
- Add label assignment with archive and bulk label widget
- Enable inviting users that haven't logged in yet #644
- Add configurable inbound auth backends #636
- Add encryption, custom scopes, levels and auditing on channels #599
- Add recursive SPF check and optional send-time validation #625
- Add tiered storage and refactor blobs/attachments
- Add mandatory TOTP field and search field in admin #667
- Add silent login support
- Make panel sections resizable
- Add read/unread action on thread action bar #659
- Add lprobe healthchecks and checksum verification for lprobe + Caddy #600

### Changed

- Improve message composer
- Switch back to Python's stdlib for email composition
- Put split thread feature behind a feature flag #624
- Show tooltip to confirm mailbox refresh
- Disable application menu when no option is available
- Focus `to` field on forward
- Align send button on the left
- Upgrade Cunningham and ui-kit
- Localize attachment separator
- Force default language on frontend #647
- Add DNS propagation delay info #654
- Allow specifying a channel id for the home feedback widget #655
- Support legacy and new widget attribute #650
- Update widget logic to latest version #649
- Refactor thread query cache management #642
- Allow reindexing from a given date
- Defer indexation tasks for better throughput
- Improve `search_reindex` bulk payload
- Move imports and reindex worker queues to dedicated containers #643
- Bump Keycloak to 26.6.1 #637

### Fixed

- Improve PST import logic
- Allow thread editor to destroy thread accesses #668
- Enforce full edit rights on thread mutations
- Fix race condition in last-editor deletion guard
- Fix thread panel header with nested label #658
- Fix label popup stacking with create-label modal #635
- Fix email parsing edge cases with UTF-8 in flanker #656
- Fix threads ordering #617
- Fix double request and flickering on search #596
- Handle non-serializable Celery task errors and stop infinite polling #633
- Quote error field and log SOCKS proxy in outbound delivery #626
- Do not mark thread as read when sending autoreply #594

### Security

- Stop flagging inbound `From=To` mails as `is_sender` #652
- Force including special characters in generated passwords #640
- Factorize SSRF code and allow redirects in image proxy #631

## [0.5.0] - 2026-03-16

### Added

- Add autoreply feature with scheduling support #569
- Add an action to split a thread from a message #561
- Add starred/important thread feature scoped per mailbox #581
- Add unread and starred filters in thread panel #581
- Add better filtering and granularity for usage metrics
- Expose `oidc_autojoin` and `identity_sync` flags in provisioning API

### Changed

- Customize thread panel bulk actions according to selection state
- Rename usage API params to be more generic #589
- Remove per-message starred in favor of thread-level starred #588

  _⚠️ This migration requires a search reindex to be run after the upgrade._

- Use `url_permalink` from Drive and limit requests to Drive resource server #587

### Fixed

- Make DNS checking more resilient
- Remove `mailbox.id` from metrics

### Security

- Prevent XSS and URL redirect in shallow navigation

## [0.4.0] - 2026-03-05

### Added

- Store thread read state per thread access #575

  _⚠️ This migration requires a search reindex to be run after the upgrade._

- Store and display the user who sent a message #574
- Display selected threads count in right panel #576
- Add skip navigation link for keyboard users #573
- Add DeployCenter backend for syncing maildomain admins #572
- Add management command to print all users of the instance

### Changed

- Bump keycloak to 26.5.4 #571
- Add migrations-check Makefile command

### Fixed

- Preserve scroll position across renders #578
- Convert newlines to `<br>` in styled text #577
- Scope labels and user_role to the requested mailbox

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

- **❗ BREAKING**: Update the Drive third party api logic to comply with the new Drive logic. Messages now interops with [Drive >= 0.13.0](https://github.com/suitenumerique/drive/releases/tag/v0.13.0)
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

[unreleased]: https://github.com/suitenumerique/messages/compare/v0.10.0...main
[0.10.0]: https://github.com/suitenumerique/messages/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/suitenumerique/messages/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/suitenumerique/messages/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/suitenumerique/messages/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/suitenumerique/messages/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/suitenumerique/messages/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/suitenumerique/messages/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/suitenumerique/messages/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/suitenumerique/messages/releases/v0.2.0
[0.1.1]: https://github.com/suitenumerique/messages/releases/v0.1.1
[0.1.0]: https://github.com/suitenumerique/messages/releases/v0.1.0

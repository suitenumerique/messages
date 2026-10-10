# Threat model

## What this project does and where untrusted input enters

Messages is the collaborative inbox of La Suite territoriale, a French
government platform (ANCT). French local authorities self-host it or use the
hosted service. It receives mail from the Internet with its own MTA, stores
it, and shows it to the several users who share one mailbox. Every byte of an
email is attacker-controlled: anybody can send a message to a hosted mailbox,
and the pipeline parses that message before any user acts on it.

The inbound MTA is in `src/mta-in` (a pure-Python `aiosmtpd` server, pymta,
and a legacy Postfix milter). The parser that turns raw RFC 5322 bytes into a
JMAP Email object is the `jmap-email` package in `src/jmap-email`. The backend
is Django 5 with Django REST Framework, in `src/backend`. The frontend is a
Vite and React single-page application in `src/frontend`, a client of the API
with no server rendering; the same code ships as a Capacitor mobile app.

Actors: anonymous Internet senders (anyone who connects to port 25, or who
posts to a feedback widget); the MTA itself, the only client of the MDA
endpoint; authenticated users (an OIDC session, `docs/authentication-provider.md`),
who hold the role VIEWER, EDITOR, SENDER or ADMIN on a mailbox, VIEWER or
EDITOR on a thread, and ADMIN on a mail domain (`src/backend/core/enums.py`,
`docs/permissions.md`); API-key clients of the channel endpoints and webhook
receivers (`docs/webhooks.md`); superusers (the Django admin).

Entry points, most exposed first:

- SMTP on port 25, `src/mta-in/`. The MTA has no queue: it validates the
  envelope, then posts the raw message to the backend during the SMTP
  session. `src/mta-in/src/pymta/` is the recommended implementation
  (`src/mta-in/README.md`): `smtp_protocol.py` (the `SMTP` subclass that
  enforces the line, command and size limits), `handler.py` (EHLO, MAIL
  FROM, RCPT TO, DATA), `address.py` (envelope address validation) and
  `limits.py` (per-address connection gating). `src/mta-in/src/delivery_milter.py`
  is the Postfix milter that `docs/self-hosting.md` still describes.
- The RFC 5322 and MIME parser, `src/jmap-email/jmap_email/`. `parser.py` is
  the core, `addresses.py` parses address lists, `filenames.py` sanitizes
  attachment filenames, `preview.py` extracts the preview text. The "Defense
  matrix" section of `src/jmap-email/README.md` lists the attacks the parser
  guards against. A bypass of any guard listed there is a finding.
- The MDA endpoint, `src/backend/core/api/viewsets/inbound/mta.py`.
  `MTAJWTAuthentication` there authenticates the channel with an HS256 JWT
  over the shared `MDA_API_SECRET` and a `body_hash` claim that must equal
  the SHA-256 of the body. Treat the endpoint as reachable by an attacker who
  has the network but not the secret.
- The feedback widget, `src/backend/core/api/viewsets/inbound/widget.py`.
  `WidgetAuthentication` accepts a channel UUID in `X-Channel-ID`. That UUID
  is a public embed value, so treat the `deliver` action as unauthenticated.
  `WidgetChannelThrottle` and `WidgetIPThrottle` are the only limits.
- Inbound processing, `src/backend/core/mda/`: `inbound.py`,
  `inbound_pipeline.py`, `inbound_create.py`, `raw_mime.py`,
  `inbound_auth.py` (the DKIM and DMARC verdict), `arc.py`, `signing.py`
  (DKIM and ARC), `replies.py`, `inline_images.py` and `spam.py`. This code
  threads messages, applies labels and stores blobs. Thread assignment
  decides who reads the message. `docs/spam.md`, "ARC relay-trust", explains
  the fail-closed allowlist of ARC sealers.
- The REST API, `src/backend/core/api/` (`core/urls.py`, viewsets in
  `core/api/viewsets/`, `core/api/permissions.py`). A mailbox is shared, so
  object-level authorization needs the most attention. Session
  authentication is enabled next to OIDC, so CSRF applies to every mutation;
  `CSRF_USE_SESSIONS = True` binds the token to the session.
- Outbound requests whose target the user or the sender influences: the
  webhook dispatcher (`core/mda/dispatch_webhooks.py`), the image proxy
  (`core/api/viewsets/image_proxy.py`), web push
  (`core/services/push/webpush.py`), the CalDAV client
  (`core/services/calendar/service.py`), the IMAP importer
  (`core/services/importer/imap.py`) and the SMTP relay lookup
  (`core/mda/outbound.py`). All of them go through `core/services/ssrf.py`.
  `docs/webhooks.md`, "Security notes", states the guarantees: scheme
  allowlist, no IP literals, IP pinning, and re-validation on every redirect
  hop. A bypass of any of them is a finding.
- The importers, `src/backend/core/services/importer/`. A user uploads an
  MBOX file (`mbox.py`), a PST file (`pst.py`, which uses `libpff`) or an EML
  file (`eml.py`), or names a remote IMAP server (`imap.py`).
- Attachments and blobs: `core/services/attachments.py`,
  `core/services/tiered_storage.py`, `core/services/blob_gc.py`,
  `core/services/s3_seekable.py` and `core/api/viewsets/blob.py`. Content
  type, filename and size all come from the message. Inbound `.ics`
  attachments go through `core/services/calendar/ics_rebuild.py`, which
  rebuilds a fresh VCALENDAR from a property allowlist; a bypass of that
  allowlist is a finding.
- Outbound composition: `src/jmap-email/jmap_email/composer.py`,
  `core/mda/outbound.py`, `core/mda/outbound_direct.py` and `core/mda/smtp.py`.
  A draft holds user text, and a reply quotes attacker text. Header injection
  and SMTP smuggling belong here.
- The web client, `src/frontend/src`. It renders an attacker-controlled HTML
  body. The three defenses are DOMPurify in
  `src/frontend/src/features/layouts/components/thread-view/components/thread-message/renderers/text_html.ts`;
  the iframe in `thread-message-body.tsx`, one directory up, which omits
  `allow-scripts` and carries a `default-src 'none'`, `script-src 'none'`
  Content-Security-Policy; and the external-link confirmation in
  `use-link-confirmation.tsx` with the host allowlist in `is-host-trusted.ts`,
  against link masking. A bypass of any of the three is a finding. The image
  proxy hides the reader from the sender.
- Other authenticated surfaces: API keys and webhooks
  (`core/api/viewsets/channel.py`), the search query parser
  (`core/services/search/`), the mobile session handoff
  (`core/api/viewsets/mobile_auth.py`), the push gateways
  (`core/services/push/`), the TOTP management of a mail domain
  (`core/api/viewsets/maildomain.py`) and the AI features (`core/ai/`, which
  send message text to a model and write the answer back as a label).

## Components that matter most / least

Most important, report findings here first:

- `src/jmap-email/jmap_email/`: every message of every tenant passes through
  it.
- `src/mta-in/src/pymta/`: the first unauthenticated code path.
- `src/backend/core/mda/`: an error in thread assignment shows mail to the
  wrong tenant.
- `src/backend/core/api/permissions.py` and the viewsets in
  `src/backend/core/api/viewsets/`: who can read or write a mailbox, a thread
  or a domain, and the queryset filtering per mailbox.
- `src/backend/core/models.py`: the access models and their role checks.
- `src/backend/core/services/ssrf.py` and its six callers, listed above.
- `src/backend/core/services/importer/`.
- The frontend sanitizer and the code around it, listed above.

Less important, but still in scope: the rest of `src/backend/`, the
management commands, `src/mta-in/src/delivery_milter.py`, and
`src/keycloak/bulk-role-membership/` (a Keycloak SPI in Java).

Out of scope as a target, do not report findings here: `src/socks-proxy/` and
`src/mpa/` hold no parser of ours. They wrap Dante and rspamd; report a bug
in our configuration of them, not a bug inside them. The same applies to
Postfix, Keycloak and OpenSearch. `src/e2e/`, `src/frontend/android/`,
`src/frontend/ios/`, `crowdin/`, the translation files, `core/migrations/`
(schema history), the test suites (`core/tests/`, `core/factories.py`,
`src/jmap-email/tests/`, `src/mta-in/tests/`), `src/keycloak/realm.json`
(the development OIDC realm), `deploy/` (read it for context), `bin/`,
`compose.yaml`, `Makefile`; third-party code in `/opt/venv`, `/opt/s3`,
`/opt/python`, `/usr/local/lib/node_modules` and `node_modules`.

## How to exercise it

The image for this scan places the checkout at `/src`. The backend is at
`/src/src/backend`, the MTA at `/src/src/mta-in`, the parser at
`/src/src/jmap-email` and the frontend at `/src/src/frontend`.
`DJANGO_CONFIGURATION=Test` and the database, Redis, S3 and MTA variables
are already set.

PostgreSQL 17, Redis, an S3 endpoint (moto) and a mail catcher (maildev on
ports 1025 and 1080, under the name `mailcatcher`) run in the image. Every
bash shell starts them. `BASH_ENV` only reaches bash, so a `sh` or `#!/bin/sh`
script must run `svcctl start` itself. Use `svcctl start`, `svcctl stop` and
`svcctl status` to control them, and `svcctl psql -d messages` to inspect
the already migrated `messages` database. moto keeps its buckets in memory,
so a blob does not survive a restart of the container. The logs are
`/var/log/moto_server.log` and `/var/log/maildev.log`.

Each component keeps its own virtual environment, because the three
`pyproject.toml` files pin different versions of the same packages:
`/opt/venv/backend`, `/opt/venv/mta-in` and `/opt/venv/jmap-email`.
`/opt/venv/backend/bin` is on `PATH` and `VIRTUAL_ENV` names that
environment, so a bare `python`, `pytest`, `ruff` or `uv pip install` acts on
the backend's. Name the other two environments by their full path.
`/opt/pymta-certs/` holds the throwaway STARTTLS pair that pymta serves.

Run the backend tests:

```sh
cd /src/src/backend
pytest --reuse-db --no-cov
```

Add `-n 2` to use both CPUs of the scan machine; `-n auto` can spawn one
worker per host CPU. Add `--create-db` after a model change. Factories are
in `core/factories.py`. Tests under `core/tests/api/` show how to call each
endpoint, and tests under `core/tests/mda/` feed raw messages through the
inbound pipeline. The two outbound tests in
`core/tests/mda/test_outbound_e2e.py` poll `http://mailcatcher:1080/email`
for what they sent. The calendar and SMTP tests start a server on a loopback
port that the OS assigns. `core/tests/api/test_calendar.py`
disables the CalDAV SSRF guard for its module; the `caldav_ssrf_real` marker
opts a test back in. The guard is present in the code.

Run the parser tests:

```sh
cd /src/src/jmap-email
/opt/venv/jmap-email/bin/pytest -q
```

The fuzz tests are excluded by default (`-m "not fuzz"` in
`src/jmap-email/pyproject.toml`). Run them with `-m fuzz`. They use
Hypothesis and read `FUZZ_EXAMPLES`, which defaults to 10000 examples, or
2000 in `test_composer_fuzz.py`; `FUZZ_EXAMPLES=200` keeps a run short. A
fuzz test is a good way to confirm a parser finding.

Run the MTA tests. `pymtactl start` starts pymta on port 25 with the
environment of the `mta-in-py-test` service of `compose.yaml`; the suite then
starts its own mock of the backend on port 8000, so stop `runserver` first:

```sh
pymtactl start
cd /src/src/mta-in
/opt/venv/mta-in/bin/pytest -q --no-cov tests
```

The Postfix milter has no server in this image. To send a crafted message
by hand, connect to `localhost:25` with `nc` or a short `smtplib` script
while `pymtactl status` says the server is up; `/var/log/pymta.log` holds
the log of the server.

Run the development server:

```sh
cd /src/src/backend
python manage.py runserver --insecure 0.0.0.0:8000
```

`Test` runs with `DEBUG=False`, so `--insecure` is what makes `runserver`
serve the collected files in `/data/static`. The swagger UI is at
`/api/v1.0/swagger/`.

Run the frontend checks, all offline:

```sh
cd /src/src/frontend
npm run test
npm run lint
npm run ts:check
```

`npm run build` also works. `tsc` on this tree needs several gigabytes of
memory, in `ts:check` and in `build` alike, and `src/frontend/dist` is not
built in the image.

## How you rate severity

Rate the impact on the confidentiality of somebody else's mail first. We host
mailboxes for public bodies, and the mail of one authority must never reach
another.

Critical: remote code execution; authentication bypass; any unauthenticated
read or write of another tenant's mail; a leak of `MDA_API_SECRET`,
`SALT_KEY`, `DJANGO_SECRET_KEY` or an OIDC client secret that an attacker
can reach.

High: an authenticated user reads or writes a mailbox or a thread that no
access grants them (an IDOR); a stored cross-site scripting that runs in the
reader's origin; SQL injection, including after authentication; server-side
request forgery that reaches our internal network; cross-site request
forgery on a state-changing endpoint; an SPF, DKIM, DMARC or ARC check that
an attacker can forge past; header injection or SMTP smuggling on the
outbound path; a path traversal in an import or in an attachment filename; a
leak of a stored API key or webhook secret.

Medium: a privilege boundary crossed inside one mailbox (for example a
VIEWER who sends); a leak of metadata, such as a subject line or an address
book; a missing rate limit on an unauthenticated path; sender text that
makes the AI features write a label the user did not choose.

Low: a hardening gap with no demonstrated impact; a crash that only the
sender's own session feels; a missing security header; a missing rate limit
on an authenticated CRUD endpoint.

Rules we apply on top:

- A denial of service that one inbound message causes is at least medium,
  because the MTA has no queue and we cannot drop the message.
- A finding that needs the ADMIN role on a mail domain is one level lower.
  That role is already trusted with the mailboxes of the domain, and it may
  force or reset the TOTP of the users of the domain by design.
- Show the path from the untrusted input to the effect.
- A report that depends on a development value from `deploy/env/` in
  production is not a finding.
- Report a bypass of a guard that the "Defense matrix" of
  `src/jmap-email/README.md` claims, even when the effect is only a parse
  difference: we rely on our parse of a message matching what the receiving
  client sees.

Not a finding: anything that exists only under the `Build`, `Development`,
`E2E`, `DevelopmentMinimal`, `Test` or `ContinuousIntegration` classes in
`src/backend/messages/settings.py`; report against `Production` and the
classes that inherit it, `Feature`, `Staging` and `PreProduction`. Reflected
content in a JSON response, because the client is a single-page application.

## Anything to leave alone

- This image runs `DJANGO_CONFIGURATION=Test`, the same configuration CI
  uses: the MD5 password hasher, `USE_SWAGGER=True`, a dummy
  `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=*`, a superuser database role
  named `user` with password `pass`, `fsync=off`, and the development values
  of `MDA_API_SECRET`, `SALT_KEY` and the S3 keys.
- Every secret in `deploy/env/*.defaults`, `src/keycloak/realm.json` and
  `compose.yaml` is a public development value, not a leak.
- The image uses PostgreSQL 17 and moto. Development and CI use PostgreSQL
  16.6 and rustfs (`compose.yaml`). This difference is intentional. maildev
  is the same release as the `mailcatcher` service of `compose.yaml`.
- OpenSearch is absent and `OPENSEARCH_URL` is empty, so the full-text
  search tests skip themselves (`core/tests/search/test_e2e.py`), and a
  thread list request with a `search` parameter raises `IndexError` in
  `core/api/viewsets/thread.py`. One test of `core/tests/api/test_threads_list.py`
  fails for that reason. Do not report that error; it exists only in this
  image. Read `core/services/search/` statically instead: `parse.py` only
  tokenizes the query, and the rest of that package builds the query from
  the tokens.
- The backend imports `jmap_email` from `/src/src/jmap-email`, the way
  `compose.yaml` mounts it for `make test-back`. The lock file pins the 0.3.0
  wheel; the checkout holds 0.3.1. Report a parser bug against the source in
  the checkout.
- `CORS_ALLOW_ALL_ORIGINS = True` is set only in the `Development` class;
  `Base` and `Production` leave it `False`. The `0.0.0.0` binds come from
  `src/backend/Dockerfile` and `compose.yaml`, not from a settings class.
  Both belong to the development stack. The production deployment is in
  `deploy/`.
- API-key channels store a SHA-256 of each key; webhook channels store the
  signing secret in clear text, because the dispatcher signs every delivery
  with it (`core/models.py`). This is a known design choice. Report only an
  exposure of a stored secret.
- An authenticated user who holds another user's raw push token can evict
  that user's push channel (`core/services/push/common.py`,
  `docs/push-notifications.md`). This is an accepted risk: the effect is a
  notification outage that heals itself, with no content disclosure.
- The mobile session exchange (`core/api/viewsets/mobile_auth.py`) is
  `AllowAny` on purpose. The one-time token is bound to a PKCE verifier,
  single-use and throttled, so an intercepted deep link alone is not enough.
  Report only a path that defeats one of those three.
- The CSRF token travels in a response body, not in a `csrftoken` cookie,
  because `CSRF_USE_SESSIONS = True`. The missing cookie is not the bug.
- Messages does not serve IMAP or POP3 to mail clients, by design
  (`README.md`). Do not report the absence of that feature. The IMAP
  importer in `core/services/importer/imap.py` is a different thing and is
  in scope.
- `ruff`, `pylint`, `eslint` and `tsc` run in CI. Do not report their
  opinions.
- A dependency CVE with no reachable call path in this code is not wanted.
- We run `pip-audit` by hand (`make deps-audit-back`,
  `make deps-audit-mta-in`); nothing watches the lock files automatically.

## How reports and patches should look

Submit one finding per report, with an input that reproduces it: a raw
message as a `.eml` file and the `smtplib` script or `nc` command that
delivers it to `localhost:25`; a `curl` command against `runserver`; or a
failing `pytest` test under `core/tests`, `src/jmap-email/tests` or
`src/mta-in/tests` that uses the factories in `core/factories.py` where a
database object is needed.

Include a patch as a diff against the component. The patch must pass:

```sh
ruff check
ruff format --check
```

Run `ruff` from the component directory, with the `ruff` of that
component's virtual environment, so it reads the right configuration. The
line length is 88 characters in `src/backend/pyproject.toml` and
`src/jmap-email/pyproject.toml`, and 99 in `src/mta-in/pyproject.toml`. A
parser patch must also pass `pylint` and keep the "Defense matrix" of
`src/jmap-email/README.md` true. A frontend patch must pass `npm run lint`
and `npm run ts:check`.

Send security questions outside this scan to security@suite.anct.gouv.fr,
per `SECURITY.md`.

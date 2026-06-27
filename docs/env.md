# Environment Variables

This document provides a comprehensive overview of all environment variables used in the Messages application. These variables are organized by service and functionality.

## Development Environment

### Environment Files Structure

The application uses a new environment file structure with `.defaults` and `.local` files:

- `*.defaults` - Committed default configurations
- `*.local` - Gitignored local overrides (created by `make bootstrap`)

#### Available Environment Files

- `backend.defaults` - Main Django application settings
- `common.defaults` - Shared settings across services
- `frontend.defaults` - Frontend configuration
- `postgresql.defaults` - PostgreSQL database configuration
- `keycloak.defaults` - Keycloak configuration
- `mta-in.defaults` - Inbound mail server settings
- `mta-out.defaults` - Outbound mail server settings
- `crowdin.defaults` - Translation service configuration

## Core Application Configuration

### Django Settings

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `DJANGO_CONFIGURATION` | `Development` | Django configuration class to use (Development, Production, Test, etc.) | Required |
| `DJANGO_SECRET_KEY` | None | Secret key for cryptographic signing | Required |
| `DJANGO_ALLOWED_HOSTS` | `[]` | List of allowed hostnames | Required |
| `DJANGO_SETTINGS_MODULE` | `messages.settings` | Django settings module | Required |
| `DJANGO_SUPERUSER_PASSWORD` | `admin` | Default superuser password for development | Dev |
| `DJANGO_DATA_DIR` | `/data` | Base directory for data storage | Optional |
| `DJANGO_ADMIN_URL` | `admin` | admin route (must not be ended by `/`) | Optional |
| `INSTANCE_URL` | None | Public base URL of this instance — the scheme+host that serves both the API and the web app (e.g. `https://messages-public-url.example.com`). Used to build absolute links back into the product; currently emitted as the `X-StMsg-Instance` header on outbound webhooks (omitted when unset). | Optional |
| `USE_X_FORWARDED_FOR` | `False` | Trust the `X-Forwarded-For` header to determine the client IP (enable only behind a trusted proxy that sets it). | Optional |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `2621440` | Django's max request body (bytes) buffered in memory before rejecting (2.5MB). | Optional |

### Database Configuration

#### PostgreSQL (Main Database)
| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `DATABASE_URL` | None | Complete database URL (overrides individual DB_* vars) | Optional |
| `DB_ENGINE` | `django.db.backends.postgresql_psycopg2` | Database engine | Optional |
| `DB_HOST` | `postgresql` | Database hostname (container name) | Optional |
| `DB_NAME` | `messages` | Database name | Optional |
| `DB_USER` | `user` | Database username | Optional |
| `DB_PASSWORD` | `pass` | Database password | Optional |
| `DB_PORT` | `5432` | Database port | Optional |

#### PostgreSQL (Keycloak)
| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `POSTGRES_DB` | `messages` | Keycloak database name | Dev |
| `POSTGRES_USER` | `user` | Keycloak database user | Dev |
| `POSTGRES_PASSWORD` | `pass` | Keycloak database password | Dev |

### Redis Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `REDIS_URL` | `redis://redis:6379` | Redis connection URL (internal) | Optional |
| `CELERY_BROKER_URL` | `redis://redis:6379` | Celery message broker URL (internal) | Optional |
| `CACHES_DEFAULT_TIMEOUT` | `30` | Default cache timeout in seconds | Optional |

**Note**: For external Redis access, use `localhost:8913`. For internal container communication, use `redis:6379`.

### OpenSearch Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `OPENSEARCH_URL` | `["http://opensearch:9200"]` | OpenSearch hosts list | Optional |
| `OPENSEARCH_CA_CERTS` | None | Path to a CA bundle for verifying the OpenSearch TLS certificate (for `https://` hosts with a private CA). | Optional |
| `OPENSEARCH_TIMEOUT` | `20` | OpenSearch query timeout (seconds) for unitary requests | Optional |
| `OPENSEARCH_BULK_TIMEOUT` | `60` | OpenSearch request timeout (seconds) applied to bulk indexation calls. Raise it if full reindex (`make search-index`) hits timeouts on large payloads. | Optional |
| `OPENSEARCH_BULK_MAX_BYTES` | `26_214_400` | Flush threshold (bytes) for bulk indexation payloads; default 25 MiB. Once accumulated actions exceed this, `opensearch-py` emits a sub-chunk HTTP request. Note: this is a batching threshold, not a per-document cap — a single oversized document is still sent as its own chunk. Keep well under the OpenSearch server `http.max_content_length` | Optional |
| `OPENSEARCH_BULK_CHUNK_SIZE` | `50` | Number of thread documents (and their child message documents) accumulated before a bulk flush in `reindex_bulk_threads`. Lower values reduce per-request cluster pressure (heap, queue depth) at the cost of more round-trips. Lower this if you see 503s on bulk requests. | Optional |
| `OPENSEARCH_MAX_RETRIES` | `3` | Transport-level retry budget on the OpenSearch client. The opensearch-py transport already retries on 502/503/504 (`DEFAULT_RETRY_ON_STATUS`); this just exposes the count so it can be raised above the library default. Whatever exhausts this budget is wrapped as `TransientTransportError` and handed to Celery autoretry (5 attempts, exponential backoff up to 600s). | Optional |
| `OPENSEARCH_INDEX_THREADS` | `True` | Enable thread indexing | Optional |
| `SEARCH_REINDEX_TASKS_INTERVAL` | `30` | Interval (seconds) between Celery Beat runs of `process_pending_reindex_task`, which drains the reindex and delete coalescing buffers and enqueues bulk thread tasks. Longer values cut Celery/OpenSearch load at the cost of search-result staleness. | Optional |
| `SEARCH_FLUSH_BATCH_SIZE` | `1000` | Maximum number of thread / message IDs handed to a single `bulk_*_task` call. This is the unit of parallelism, retry granularity and worker occupation for catch-up flows. Lower means more, shorter tasks (better parallelism, cheaper retries on failure); higher means fewer, longer tasks (less broker chatter but worse failure isolation). | Optional |
| `SEARCH_FLUSH_MAX_BATCHES` | `10` | Maximum number of `bulk_*_task` calls a single Beat tick is allowed to enqueue, shared across the three handoffs (reindex / thread-delete / message-delete). Bounds catch-up bursts so a huge backlog is spread across several ticks rather than flooding the broker in one go. Effective per-tick capacity is roughly `SEARCH_FLUSH_BATCH_SIZE × SEARCH_FLUSH_MAX_BATCHES` IDs. | Optional |

## Mail Processing Configuration

### MTA Settings

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MTA_OUT_MODE` | `direct` | Outbound MTA mode ('direct' or 'relay') | Required |
| `MTA_OUT_RELAY_HOST` | `mta-out:587` | Outbound SMTP server host for relay mode | Required |
| `MTA_OUT_RELAY_USERNAME` | `user` | Outbound SMTP username for relay mode | Optional |
| `MTA_OUT_RELAY_PASSWORD` | `pass` | Outbound SMTP password for relay mode | Optional |
| `MTA_OUT_DIRECT_PROXIES` | `[]` | List of SOCKS proxy URLs (randomly chosen when non-empty; used in direct mode) | Optional |
| `MTA_OUT_DIRECT_PORT` | `25` | TCP port for direct mode on remote MX servers | Optional |
| `MTA_OUT_SMTP_TLS_SECURITY_LEVEL` | `may` | SMTP TLS security level: `none`, `may` (opportunistic, no cert check, matches Postfix), or `secure` (mandatory TLS + CA chain + hostname check). Applied to both direct and relay modes — set to `secure` when running against a controlled relay with a valid cert. | Optional |
| `MDA_API_SECRET` | `my-shared-secret-mda` | Shared secret for MDA API | Required |
| `MDA_API_BASE_URL` | `http://backend-dev:8000/api/v1.0/` | Base URL for MDA API | Dev |

### Email Domain Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MESSAGES_TESTDOMAIN` | `example.local` | Test domain for development | Dev |
| `MESSAGES_TESTDOMAIN_MAPPING_BASEDOMAIN` | `example.com` | Base domain mapping | Dev |
| `MESSAGES_ACCEPT_ALL_EMAILS` | `False` | Accept emails to any domain | Optional |

### DKIM Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MESSAGES_DKIM_SELECTOR` | `default` | DKIM selector | Optional |
| `MESSAGES_DKIM_DEFAULT_SELECTOR` | `stmessages` | Default DKIM selector applied to managed domains that don't override it. | Optional |
| `MESSAGES_DKIM_DOMAINS` | `[]` | List of domains for DKIM signing | Optional |
| `MESSAGES_DKIM_PRIVATE_KEY_B64` | None | Base64 encoded DKIM private key | Optional |
| `MESSAGES_DKIM_PRIVATE_KEY_FILE` | None | Path to DKIM private key file | Optional |
| `MESSAGES_DKIM_VERIFY_OUTGOING` | `False` | Verify the DKIM signature on outgoing messages before sending. | Optional |
| `MESSAGES_SPF_CHECK_OUTGOING` | `False` | Block outgoing messages when the sending domain's SPF includes are not correctly set up. | Optional |

## Storage Configuration

### S3-Compatible Storage

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `AWS_S3_ENDPOINT_URL` | `http://objectstorage:9000` | S3 endpoint URL | Optional |
| `AWS_S3_ACCESS_KEY_ID` | `messages` | S3 access key | Optional |
| `AWS_S3_SECRET_ACCESS_KEY` | `password` | S3 secret key | Optional |
| `AWS_S3_SIGNATURE_VERSION` | `s3v4` | S3 request signature version | Optional |
| `AWS_S3_DOMAIN_REPLACE` | None | If set, rewrites the host of generated S3 URLs to this value — e.g. map an internal endpoint to a public one for presigned/download links. | Optional |
| `AWS_S3_REGION_NAME` | None | S3 region | Optional |
| `AWS_STORAGE_BUCKET_NAME` | `st-messages-media-storage` | S3 bucket name | Optional |
| `AWS_S3_UPLOAD_POLICY_EXPIRATION` | `86400` | Upload policy expiration (24h) | Optional |
| `ITEM_FILE_MAX_SIZE` | `5368709120` | Max file size (5GB) | Optional |

### Message Imports Storage

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `STORAGE_MESSAGE_IMPORTS_ENDPOINT_URL` | `http://objectstorage:9000` | S3 endpoint URL | Required |
| `STORAGE_MESSAGE_IMPORTS_BUCKET_NAME` | `msg-imports` | S3 bucket name | Required |
| `STORAGE_MESSAGE_IMPORTS_ACCESS_KEY` | `st-messages` | S3 access key | Required |
| `STORAGE_MESSAGE_IMPORTS_SECRET_KEY` | `password` | S3 secret key | Required |
| `STORAGE_MESSAGE_IMPORTS_REGION_NAME` | None | S3 region | Optional |
| `STORAGE_MESSAGE_IMPORTS_EXPIRE_POLICY` | `3600` | Upload policy expiration (1h) | Optional |

### Tiered Blob Storage

Blobs (raw email bodies and attachments) live in PostgreSQL by default and
can be offloaded to S3 after a configurable age. See [tiered storage
docs](tiered-storage.md) for the runbook (rotation, verify, recovery).

The bucket is treated as "configured" when at least one of
`STORAGE_MESSAGE_BLOBS_ENDPOINT_URL` or `STORAGE_MESSAGE_BLOBS_ACCESS_KEY`
is set — the periodic offload task and the read-from-S3 path both
short-circuit otherwise. Bucket creds must additionally be valid for
the periodic task to actually move data; until both
`MESSAGES_BLOBS_OFFLOAD_ENABLED=True` and creds are in place, every
blob stays in PG.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `STORAGE_MESSAGE_BLOBS_ENDPOINT_URL` | unset | S3 endpoint URL for blob bucket | Optional |
| `STORAGE_MESSAGE_BLOBS_BUCKET_NAME` | unset | S3 bucket name (must be set together with `STORAGE_MESSAGE_BLOBS_ENDPOINT_URL` to enable offload) | Optional |
| `STORAGE_MESSAGE_BLOBS_ACCESS_KEY` | unset | S3 access key | Optional |
| `STORAGE_MESSAGE_BLOBS_SECRET_KEY` | unset | S3 secret key | Optional |
| `STORAGE_MESSAGE_BLOBS_REGION_NAME` | unset | S3 region | Optional |
| `MESSAGES_BLOBS_OFFLOAD_ENABLED` | `False` | Master switch for the periodic offload task. Hourly schedule with a 55-minute per-tick budget; processes blobs sequentially (no per-blob fan-out). The orphan-blob GC sweep (`gc_orphan_blobs_task`) runs on the same hourly cadence regardless of this flag — its job is reference-graph cleanup, not S3 offload. | Optional |
| `MESSAGES_BLOBS_OFFLOAD_DELAY` | `86400` | Age threshold (seconds) for offload (`0` = immediate) | Optional |
| `MESSAGES_BLOBS_OFFLOAD_MIN_SIZE` | `0` | Minimum blob size in bytes (0 = all) | Optional |
| `MESSAGES_BLOBS_COMPRESS` | `zstd:7` | Default compression: `none`, `zstd`, or `zstd:<level>` | Optional |
| `MESSAGES_BLOBS_ENCRYPT_KEYS` | `{}` | JSON dict mapping `key_id` → entry. Each entry must be `{"algo": "aes-gcm", "secret": "<32+ chars>", "active": <bool>}`. Add `"active": true` to exactly one entry to make it the key new blobs are encrypted with; entries without `active` (or with `active=false`) stay readable for legacy ciphertext. The secret is SHA-256'd to a 32-byte AEAD key, so its strength is whatever entropy the operator supplied — use `openssl rand -base64 32` (or equivalent). Startup emits a warning when a secret is shorter than 32 characters; that floor is a length check only, not an entropy measurement. | Optional |
| `MESSAGES_BLOBS_VERIFY_HASH` | `False` | When True, `Blob.get_content()` re-hashes plaintext and rejects mismatches. One SHA-256 over the plaintext per read; main value is for `key_id=0` blobs (encrypted blobs are already AAD-bound). | Optional |

### Mobile OTA Bundles

Public (anonymous read) bucket holding the mobile OTA artifacts, one
self-contained folder per release channel (`channels/<channel>/manifest.json` +
`channels/<channel>/bundles/<version>.zip`). Created with `make mobile-ota-bucket`;
bundles are published with `make ota-publish [CHANNEL=…]`. Channels are fully
independent: `NEXT_PUBLIC_*` vars are inlined into the bundle at build time, so
each channel ships its own build — never copy a bundle across channels.

Except for `MOBILE_OTA_MANIFEST_URL` (a **backend** setting served to the apps
through the `/config` endpoint), these are **frontend-toolchain / publish-time**
variables (read by `src/frontend/scripts/*-ota*.mjs`, not by Django). They live
in the frontend env files; in CI they come from secrets.

> **Opt-in in dev**: they ship **commented out** in
> `deploy/env/frontend.defaults` (the values below are the working
> dev-stack ones) — uncomment them, along with `MOBILE_OTA_MANIFEST_URL` in
> `backend.local`, to exercise the OTA chain locally.
> Hot reload (`MOBILE_DEV_SERVER_URL`, on by default in dev) skips the startup
> OTA check; disable it to test OTA end to end.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MOBILE_OTA_S3_ENDPOINT` | `http://objectstorage:9000` | S3 endpoint the script **writes** to (compose network in dev; target S3 in CI) | Optional |
| `MOBILE_OTA_S3_BUCKET` | `messages-ota` | S3 bucket name for OTA bundles | Optional |
| `MOBILE_OTA_S3_ACCESS_KEY` | `st-messages` | S3 access key | Optional |
| `MOBILE_OTA_S3_SECRET_KEY` | `password` | S3 secret key | Optional |
| `MOBILE_OTA_S3_REGION` | `us-east-1` | S3 region | Optional |
| `MOBILE_OTA_S3_KEY_PREFIX` | `` (empty) | Object key prefix; empty for a dedicated bucket root, `messages/mobileapp/` for a shared bucket. Must stay consistent with `MOBILE_OTA_PUBLIC_BASE_URL` | Optional |
| `MOBILE_OTA_CHANNEL` | `dev` (dev env) | Release channel `mobile:ota:publish` targets (`channels/<channel>/…`); overridable per run with `--channel` / `make ota-publish CHANNEL=…`. The deploy pipeline uses `staging` and `prod`, each publishing its own build. Must match the channel segment of the `MOBILE_OTA_MANIFEST_URL` served by the backend this deployment's apps talk to | Optional |
| `MOBILE_OTA_MANIFEST_URL` | None (**backend** env) | OTA channel manifest URL the apps poll at startup, served through the `/config` endpoint — so the followed channel can change without shipping a new native build. Must point to the channel this deployment publishes to. Unset disables OTA | Optional |
| `MOBILE_OTA_PUBLIC_BASE_URL` | `http://localhost:8906/messages-ota` | Device-reachable **read** base URL written into the manifest | Optional |
| `MOBILE_OTA_SIGNING_PRIVATE_KEY_B64` | None | Base64-encoded (single-line) RSA private-key PEM signing/encrypting each bundle at publish time (`publish-ota.mjs`). CI secret only — never commit. Unset ⇒ publish fails. See [mobile.md](./mobile.md#generating-the-signing-key-pair) | Optional |
| `MOBILE_OTA_SIGNING_PUBLIC_KEY_B64` | None | Base64-encoded (single-line) RSA public-key PEM baked into the app at `cap sync` time (`capacitor.config.ts`, native verification) **and** inlined by Vite into the JS bundle (`ota.ts` refuses a server-provided manifest URL on a key-less build). Read by the builds, not the publish scripts — an OTA-enabled build can never apply an unsigned bundle | Required if OTA enabled |
| `MOBILE_OTA_BUILD_ID` | `<count>-<sha>` (git-derived) | Hybrid release id stamped into the builtin bundle version at `cap sync` (`capacitor.config.ts`) and used as the default OTA `VERSION`. Computed by the Makefile from git; override in CI to pin a build. See [mobile.md](./mobile.md#bundle-versioning) | Optional |

### Static Files

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `STORAGES_STATICFILES_BACKEND` | `django.contrib.staticfiles.storage.StaticFilesStorage` | Static files storage backend | Optional |

## Authentication & Authorization

### OIDC Configuration

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `OIDC_CREATE_USER` | `False` | Automatically create users from OIDC | Optional |
| `OIDC_RP_CLIENT_ID` | `messages` | OIDC client ID | Required |
| `OIDC_RP_CLIENT_SECRET` | `ThisIsAnExampleKeyForDevPurposeOnly` | OIDC client secret | Required |
| `OIDC_RP_SIGN_ALGO` | `RS256` | OIDC signing algorithm | Optional |
| `OIDC_RP_SCOPES` | `openid email` | OIDC scopes | Optional |
| `OIDC_OP_JWKS_ENDPOINT` | `http://keycloak:8000/realms/messages/protocol/openid-connect/certs` | OIDC JWKS endpoint | Required |
| `OIDC_OP_AUTHORIZATION_ENDPOINT` | `http://localhost:8902/realms/messages/protocol/openid-connect/auth` | OIDC authorization endpoint | Required |
| `OIDC_OP_TOKEN_ENDPOINT` | `http://keycloak:8000/realms/messages/protocol/openid-connect/token` | OIDC token endpoint | Required |
| `OIDC_OP_USER_ENDPOINT` | `http://keycloak:8000/realms/messages/protocol/openid-connect/userinfo` | OIDC user info endpoint | Required |
| `OIDC_OP_LOGOUT_ENDPOINT` | None | OIDC logout endpoint | Optional |
| `OIDC_USERINFO_ESSENTIAL_CLAIMS` | `[]` | Essential OIDC claims | Optional |
| `OIDC_USERINFO_FULLNAME_FIELDS` | `["first_name", "last_name"]` | Fields to use for full name | Optional |
| `OIDC_STORE_ACCESS_TOKEN` | `False` | Store access token | Optional |
| `OIDC_STORE_REFRESH_TOKEN` | `False` | Store refresh token | Optional |
| `OIDC_STORE_REFRESH_TOKEN_KEY` | `None` | Refresh token encryption key (Must be a valid Fernet key) | Optional |


### OIDC Advanced Settings

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `OIDC_USE_NONCE` | `True` | Use nonce in OIDC flow | Optional |
| `OIDC_REDIRECT_REQUIRE_HTTPS` | `False` | Require HTTPS for redirects | Optional |
| `OIDC_REDIRECT_ALLOWED_HOSTS` | `["http://localhost:8902", "http://localhost:8900"]` | Allowed redirect hosts | Optional |
| `OIDC_STORE_ID_TOKEN` | `True` | Store ID token | Optional |
| `OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION` | `True` | Use email as fallback identifier | Optional |
| `OIDC_ALLOW_DUPLICATE_EMAILS` | `False` | Allow duplicate emails (⚠️ Security risk) | Optional |
| `OIDC_AUTH_REQUEST_EXTRA_PARAMS` | `{"acr_values": "eidas1"}` | Extra parameters for auth requests | Optional |
| `OIDC_AUTH_REQUEST_FORWARDED_PARAMS` | `["login_hint"]` | Forwarded parameters for auth requests | Optional |

### User Mapping (⚠️ DEPRECATED)
_Those settings are deprecated and will be removed in the future._

| Variable | Default | Description | Required | ⚠️ Deprecated |
|----------|---------|-------------|----------|----------|
| `USER_OIDC_ESSENTIAL_CLAIMS` | `[]` | Essential OIDC claims | Optional | Renamed to `OIDC_USERINFO_ESSENTIAL_CLAIMS` |
| `USER_OIDC_FIELDS_TO_FULLNAME` | `["first_name", "last_name"]` | Fields for full name | Optional | Renamed to `OIDC_USERINFO_FULLNAME_FIELDS` |
| `USER_OIDC_FIELD_TO_SHORTNAME` | `first_name` | Field for short name | Optional | Unused, will be removed in the future |

### Authentication URLs

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `LOGIN_REDIRECT_URL` | `http://localhost:8900` | Post-login redirect URL | Optional |
| `LOGIN_REDIRECT_URL_FAILURE` | `http://localhost:8900` | Login failure redirect URL | Optional |
| `LOGOUT_REDIRECT_URL` | `http://localhost:8900` | Post-logout redirect URL | Optional |
| `ALLOW_LOGOUT_GET_METHOD` | `True` | Allow GET method for logout | Optional |

### Mobile App Build (Capacitor)

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MOBILE_APP_ID` | `local.suitenumerique.messages` | Store/OS bundle identifier of the native app. The repo ships a neutral placeholder; an organisation publishing to the App Store / Play Store overrides it with its own signed id. Read by `cap sync` (container) **and** the native builds — gradle `applicationId`, iOS `PRODUCT_BUNDLE_IDENTIFIER` — so it must be exported in **both** the container env and the host/CI env. Independent of the auth callback scheme (`stmessages`). | Optional |
| `MOBILE_DEV_SERVER_URL` | `http://localhost:8900` (dev env, `frontend.defaults`) | **Dev only.** URL of the Vite dev server baked as Capacitor `server.url` at `cap sync` (`capacitor.config.ts`): the WebView then loads the app from Vite with hot reload instead of the embedded bundle. To disable (embedded bundle / OTA testing), set it **empty** in `frontend.local` and rerun `make mobile-build`. Must never be set for a release build — a gradle guard fails Android release builds carrying it. See [mobile.md](./mobile.md#hot-reload-on-by-default-in-dev) | Optional |
| `MOBILE_ALLOW_CLEARTEXT_FOR_DEV` | `1` (dev env, `frontend.defaults`) | **Dev only.** Baked as Capacitor `server.cleartext` at `cap sync` (`capacitor.config.ts`), i.e. `android:usesCleartextTraffic` in the Android manifest: allows plain HTTP for the whole app — the WebView reaching the Vite dev server and the native fetch/OTA layer reaching the `http://localhost:8901` backend and the RustFS OTA bucket (needed even with hot reload disabled). Must never be set for a release build — the manifest then stays cleartext-free. iOS equivalent: `NSAllowsLocalNetworking` (`Info.plist`, manual). | Optional |

> **Note**: overriding `MOBILE_APP_ID` only changes the app identity; it does **not** touch the OIDC deep-link scheme (`stmessages`), which is fixed and declared in the iOS `Info.plist` (`CFBundleURLTypes`) and the Android manifest.

### Mobile App Authentication (Capacitor)

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MOBILE_AUTH_CALLBACK_SCHEMES` | `[]` | JSON list of deep-link schemes the OIDC callback may redirect to after a mobile-initiated login (e.g. `["stmessages"]`). An empty list disables the mobile session handoff. | Optional |
| `MOBILE_AUTH_TOKEN_TTL` | `60` | Lifetime (seconds) of the one-time token a mobile app exchanges for its session cookie on `/api/v1.0/mobile/auth/exchange/` | Optional |
| `API_MOBILE_AUTH_EXCHANGE_THROTTLE_RATE` | `10/minute` | Per-IP rate limit on the anonymous `/api/v1.0/mobile/auth/exchange/` endpoint. A legitimate login exchanges once; the cap only slows down brute-force guessing of the one-time token | Optional |

> **Note**: mobile builds of the frontend must set `NEXT_PUBLIC_API_ORIGIN` explicitly — inside the Capacitor WebView there is no meaningful `window.location.origin` fallback.

## Security & CORS

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `CORS_ALLOW_ALL_ORIGINS` | `True` | Allow all CORS origins | Optional |
| `CORS_ALLOWED_ORIGINS` | `[]` | Specific allowed CORS origins | Optional |
| `CORS_ALLOWED_ORIGIN_REGEXES` | `[]` | Regex patterns for allowed origins | Optional |
| `CSRF_TRUSTED_ORIGINS` | `["http://localhost:8900", "http://localhost:8901"]` | Trusted origins for CSRF | Optional |
| `ALLOWED_HOSTS` | `[]` | Django host/domain allow-list setting (`settings.ALLOWED_HOSTS`). In the Base/Production configurations it is populated from the `DJANGO_ALLOWED_HOSTS` env var (see above); the Development configuration hardcodes `["*"]`. The `bucket_cors` management command also reads it to build S3 CORS origins. | Optional |
| `SERVER_TO_SERVER_API_TOKENS` | `[]` | API tokens for server-to-server auth | Optional |
| `SSRF_ALLOWED_HOSTS` | `[]` | Comma-separated list of exact, case-insensitive hostnames that bypass the SSRF private/internal-IP checks (webhook URLs, image proxy, IMAP, CalDAV). Use only for trusted destinations that resolve to a private address from inside the platform network (e.g. app-to-app traffic on an internal overlay). Each entry is a deliberate hole in the SSRF protection — keep the list as narrow as possible. | Optional |
| `SALT_KEY` | `[]` | Key(s) for Django Fernet-encrypted model fields. Accepts a list for rotation (`["new_key", "old_key"]`); the first is used to encrypt, all are tried to decrypt. | Optional |

## Monitoring & Observability

### Sentry

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SENTRY_DSN` | None | Sentry DSN for error tracking, shared with the frontend through the `/config` endpoint | Optional |
| `NEXT_PUBLIC_SENTRY_DSN` | None | **Deprecated** (build-time fallback, will be removed) — use `SENTRY_DSN` | Optional |
| `NEXT_PUBLIC_SENTRY_ENVIRONMENT` | None | **Deprecated** (build-time fallback, will be removed) — the frontend now uses the backend `ENVIRONMENT` | Optional |

### Selfcheck

End-to-end mail delivery probe — see [selfcheck.md](selfcheck.md) for details.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MESSAGES_SELFCHECK_FROM` | None | Email address the selfcheck sends from. Leave unset to disable the selfcheck. | Optional |
| `MESSAGES_SELFCHECK_TO` | None | Email address the selfcheck sends to. Leave unset to disable the selfcheck. | Optional |
| `MESSAGES_SELFCHECK_SECRET` | `self-check-secret-for-dev` | Secret string embedded in the test message body | Optional |
| `MESSAGES_SELFCHECK_INTERVAL` | `600` | Interval between selfcheck runs, in seconds | Optional |
| `MESSAGES_SELFCHECK_TIMEOUT` | `60` | Timeout for message reception, in seconds | Optional |
| `MESSAGES_SELFCHECK_WEBHOOK_URL` | None | Webhook URL POSTed on each successful selfcheck (updown.io-compatible heartbeat) | Optional |
| `MESSAGES_SELFCHECK_SENTRY_MONITOR_SLUG` | None | Sentry cron monitor slug. When set (with `SENTRY_DSN`), each run is reported as a Sentry check-in. | Optional |

### Logging

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `LOGGING_LEVEL_LOGGERS_ROOT` | `INFO` | Root logger level | Optional |
| `LOGGING_LEVEL_LOGGERS_APP` | `INFO` | Application logger level | Optional |
| `LOGGING_LEVEL_HANDLERS_CONSOLE` | `INFO` | Console handler level | Optional |

### Prometheus

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `ENABLE_PROMETHEUS` | `False` | Enable Prometheus monitoring | Optional |
| `PROMETHEUS_API_KEY` | None | Bearer token required to access metrics. If unset, the endpoint is public. Set this in production. | Optional |

### OpenAPI Schema

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SPECTACULAR_SETTINGS_ENABLE_DJANGO_DEPLOY_CHECK` | `False` | Enable deploy check in OpenAPI | Optional |

## Frontend Configuration

The frontend is configured at runtime through the backend `/api/v1.0/config/` endpoint (see the backend [Frontend settings](#frontend) section). The only build-time variable left is the API origin, needed to reach that endpoint.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `NEXT_PUBLIC_API_ORIGIN` | `http://localhost:8901` | Frontend API origin | Dev |

The following build-time variables are **deprecated**: they only act as fallbacks when the backend does not provide the corresponding setting, and will be removed in a future release.

| Deprecated variable | Replaced by (backend setting) |
|----------|---------|
| `NEXT_PUBLIC_LANGUAGES` | `LANGUAGES` |
| `NEXT_PUBLIC_DEFAULT_LANGUAGE` | `LANGUAGE_CODE` |
| `NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE` | `FRONTEND_FORCED_DEFAULT_LANGUAGE` |
| `NEXT_PUBLIC_THEME_CONFIG` | `FRONTEND_THEME_CONFIG` |
| `NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE` | `FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB` |
| `NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL` | `FRONTEND_FEEDBACK_WIDGET_CONFIG` (`api_url` key) |
| `NEXT_PUBLIC_FEEDBACK_WIDGET_PATH` | `FRONTEND_FEEDBACK_WIDGET_CONFIG` (`path` key) |
| `NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL` | `FRONTEND_FEEDBACK_WIDGET_CONFIG` (`channel` key) |
| `NEXT_PUBLIC_FEEDBACK_WIDGET_HOME_CHANNEL` | `FRONTEND_FEEDBACK_WIDGET_CONFIG` (`home_channel` key) |
| `NEXT_PUBLIC_HELP_CENTER_URL` | `FRONTEND_HELP_CENTER_URL` |
| `NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL` | `FRONTEND_LAGAUFRE_WIDGET_CONFIG` (`api_url` key) |
| `NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH` | `FRONTEND_LAGAUFRE_WIDGET_CONFIG` (`path` key) |
| `NEXT_PUBLIC_SENTRY_DSN` | `SENTRY_DSN` |
| `NEXT_PUBLIC_SENTRY_ENVIRONMENT` | `ENVIRONMENT` (backend environment) |
| `NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL` | `MOBILE_OTA_MANIFEST_URL` |

## Development Tools

### Crowdin (Translations)

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `CROWDIN_PERSONAL_TOKEN` | `Your-Personal-Token` | Crowdin API token | Dev |
| `CROWDIN_PROJECT_ID` | `Your-Project-Id` | Crowdin project ID | Dev |
| `CROWDIN_BASE_PATH` | `/app/src` | Base path for translations | Dev |

## Application Settings

### Common Feature Flags

Kill-switches for opt-out features. Flip to `False` to disable the
corresponding API action and hide the related frontend entry points,
without redeploying the frontend (the flag is pulled from
`GET /api/v1.0/config/`).

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `FEATURE_IMPORT_MESSAGES` | `True` | Enables message import (IMAP, PST, MBOX, etc.). When `False`, mailbox admins lose the `CAN_IMPORT_MESSAGES` ability. | Optional |
| `FEATURE_MAILBOX_ADMIN_CHANNELS` | `api_key,webhook` | Comma-separated list of channel types enabled for mailbox admin (e.g., `widget,api_key`). Empty list disables all channel types. | Optional |
| `FEATURE_MAILDOMAIN_CREATE` | `True` | Allows superusers to create new mail domains via the API. When `False`, the create action returns 403. | Optional |
| `FEATURE_MAILDOMAIN_MANAGE_ACCESSES` | `True` | Allows managing mail domain accesses (create/delete). When `False`, those actions return 403. | Optional |
| `FEATURE_MESSAGE_TEMPLATES` | `True` | Enables the "message templates" feature. When `False`, mailbox admins lose the `CAN_MANAGE_MESSAGE_TEMPLATES` ability and the related UI is hidden. | Optional |
| `FEATURE_THREAD_SPLIT` | `True` | Enables "split thread" feature. When `False`, the split API action returns 404 and the frontend hides the menu entry. | Optional |
| `FEATURE_MAILDOMAIN_MANAGE_TOTP` | `False` | Enables the "Mandatory 2FA" (TOTP) toggle for mail domains. Requires the Keycloak identity-provider settings and `KEYCLOAK_TOTP_ROLE_ID`. | Optional |

### Business Logic

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `TRASHBIN_CUTOFF_DAYS` | `30` | Days before permanent deletion | Optional |
| `INVITATION_VALIDITY_DURATION` | `604800` | Invitation validity (7 days) | Optional |
| `MESSAGES_MANUAL_RETRY_MAX_AGE`| `604800` | Maximum age in seconds for a message to be eligible for manual retry of failed deliveries (7 days) | Optional |
| `MESSAGES_INBOUND_DEFERRAL_MAX_AGE` | `172800` | Maximum age in seconds an inbound message is deferred (retried every 5 min) when a processing step keeps failing, before the pipeline delivers it anyway (recorded as `postmark["processing"]`) rather than holding it indefinitely (48 hours) | Optional |
| `MAX_INCOMING_EMAIL_SIZE` | `10485760` | Maximum size in bytes for incoming email (including attachments and body) (10MB) | Optional |
| `MAX_OUTGOING_ATTACHMENT_SIZE` | `20971520` | Maximum size in bytes for outgoing email attachments (20MB) | Optional |
| `MAX_OUTGOING_BODY_SIZE` | `5242880` | Maximum size in bytes for outgoing email body (text + HTML) (5MB) | Optional |
| `MAX_TEMPLATE_IMAGE_SIZE` | `2097152` | Maximum size in bytes for images embedded in templates and signatures (2MB) | Optional |
| `MAX_RECIPIENTS_PER_MESSAGE` | `500` | Maximum number of recipients per message (to + cc + bcc) | Optional |
| `MAX_THREAD_EVENT_EDIT_DELAY` | `3600` | Time window in seconds during which a ThreadEvent (internal comment) can still be edited or deleted after creation. Set to `0` to disable the restriction. | Optional |
| `MESSAGES_ALLOW_INTERNAL_DELIVERY` | `True` | Deliver mailbox-to-mailbox mail through the internal inbound pipeline (fast path). Set `False` to force same-instance mail out through the external MTA so it passes the same scanning/archiving as outbound. | Optional |
| `MESSAGES_MAILBOX_LOCALPART_DENYLIST_PERSONAL` | `[]` | Local parts rejected for personal mailboxes (case-insensitive exact match). | Optional |

### Model custom attributes schema

**Note**: Custom attributes are stored in a JSONField (Take a look at User and MailDomain models).

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SCHEMA_CUSTOM_ATTRIBUTES_USER` | {} | JSONSchema definition of the User custom attributes | Optional |
| `SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN` | {} | JSONSchema definition of the MailDomain custom attributes | Optional |

### Internationalization

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `LANGUAGE_CODE` | `en-us` | Default backend language code | Optional |


### AI

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `AI_BASE_URL` | None | Default URL to access AI API endpoint (Albert API) | Optional |
| `AI_API_KEY` | None| API Key used for AI features | Optional |
| `AI_MODEL` | None | Default model used for AI features | Optional |
| `FEATURE_AI_SUMMARY` | `False` | Default enabled mode for summary AI features | Required |
| `FEATURE_AI_AUTOLABELS` | `False` | Default enabled mode for label AI features | Required |

### Push Notifications

Push notifications for new messages, delivered to iOS (APNs), Android (FCM) and web browsers (Web Push / VAPID). The feature is **off by default** (`PUSH_ENABLED=False`): no tokens are pushed to and no external gateway is contacted. To go live, set `PUSH_ENABLED=True` **and** fully configure at least one gateway below.

Each gateway is all-or-nothing, and validated at boot: with `PUSH_ENABLED=True`, each gateway's variables must be set **together or not at all** (e.g. `PUSH_VAPID_PRIVATE_KEY`, `PUSH_VAPID_PUBLIC_KEY` and `PUSH_VAPID_SUBJECT` for Web Push), otherwise Django raises a `ValueError` at startup — a partial group would enroll devices that never receive anything. Device registration additionally refuses (400) any platform whose gateway is absent altogether, and a sender that finds itself without credentials at send time (removed *after* devices enrolled) drops with a deduplicated warning rather than silently. A deployment configuring only some gateways (e.g. native-only, no VAPID vars) boots and runs fine.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `PUSH_ENABLED` | `False` | Master switch. When `False`, the feature is fully dark: no gateway is contacted and the enqueue helper never schedules the Celery task. | Optional |
| `PUSH_APNS_KEY` | None | Contents of the APNs auth key `.p8` file (PEM). Required for iOS (with the three vars below). | Optional |
| `PUSH_APNS_KEY_ID` | None | APNs auth key id (Key ID from the Apple developer portal). | Optional |
| `PUSH_APNS_TEAM_ID` | None | Apple developer Team ID. | Optional |
| `PUSH_APNS_BUNDLE_ID` | None | App bundle id, used as the APNs topic. | Optional |
| `PUSH_APNS_USE_SANDBOX` | `False` | `False` targets Apple's production gateway; `True` the sandbox gateway (only accepts tokens from a development-signed build). | Optional |
| `PUSH_FCM_CREDENTIALS` | None | Firebase service-account JSON (the whole file contents) as a string. Required for Android (with `PUSH_FCM_PROJECT_ID`). | Optional |
| `PUSH_FCM_PROJECT_ID` | None | Firebase project id; selects the FCM HTTP v1 endpoint. Separate staging from production by pointing at a different Firebase project, not a flag. | Optional |
| `PUSH_VAPID_PRIVATE_KEY` | None | VAPID application-server private key (PEM or base64url). Required for Web Push (see boot coupling above). Rotating it orphans every existing web subscription — clients must re-subscribe — and requires re-deriving the public key. | Optional |
| `PUSH_VAPID_PUBLIC_KEY` | None | Matching VAPID public key (base64url, the uncompressed P-256 point). Served verbatim via `/config` as the browser's `applicationServerKey`; public by definition. Derive it from the private key with `python manage.py derive_vapid_public_key` (add `--verify` to check the pair matches). | Optional |
| `PUSH_VAPID_SUBJECT` | None | VAPID `sub` claim; must be a `mailto:` or `https:` URI, e.g. `mailto:ops@example.com`. | Optional |
| `PUSH_MAX_DEVICES_PER_USER` | `20` | Hard ceiling on push devices one user may keep. Registering beyond it prunes the least-recently-used device(s). | Optional |

### Throttling

Outbound message throttling limits the number of **external recipients** (recipients whose domain is not managed by this instance) that can be sent from a mailbox or maildomain within a time period, using simple fixed time windows.


| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `THROTTLE_MAILBOX_OUTBOUND_EXTERNAL_RECIPIENTS` | None | Rate limit per mailbox. Format: `count/period` where period is `minute`, `hour`, or `day`. Example: `1000/day` limits each mailbox to 1000 external recipients per day. | Optional |
| `THROTTLE_MAILDOMAIN_OUTBOUND_EXTERNAL_RECIPIENTS` | None | Rate limit per maildomain. Format: `count/period`. Example: `10000/day` limits each domain to 10000 external recipients per day. | Optional |
| `THROTTLE_AUTOREPLY_PER_SENDER` | `1/day` | Rate limit for autoreplies per sender per mailbox. Format: `count/period`. Example: `1/day` limits each sender to 1 autoreply per day per mailbox. | Optional |
| `API_USERS_LIST_THROTTLE_RATE_SUSTAINED` | `180/hour` | Sustained rate limit on the users-list API (per user). | Optional |
| `API_USERS_LIST_THROTTLE_RATE_BURST` | `30/minute` | Burst rate limit on the users-list API (per user). | Optional |
| `API_CALDAV_CONFLICTS_THROTTLE_RATE` | `30/minute` | Rate limit on the CalDAV conflict-check API. | Optional |
| `API_WIDGET_INBOUND_CHANNEL_THROTTLE_RATE` | `30/minute` | Rate limit on inbound widget submissions, per widget channel. | Optional |
| `API_WIDGET_INBOUND_IP_THROTTLE_RATE` | `10/minute` | Per-IP burst limit on inbound widget submissions. | Optional |
| `API_DEVICE_REGISTRATION_THROTTLE_RATE` | `30/hour` | Per-user rate limit on push device (re)registration. Clients re-register on every cold launch and on token rotation, so this is deliberately loose; the hard ceiling on distinct devices is `PUSH_MAX_DEVICES_PER_USER`. | Optional |

### Image Proxy

**Note**: By default `IMAGE_PROXY_MAX_SIZE` is set to 5MB. We do not encourage to increase this value as
it can lead to memory exhaustion, increase at your own risk.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `IMAGE_PROXY_ENABLED` | `False` | Whether external images should be proxied | Optional |
| `IMAGE_PROXY_MAX_SIZE` | `5242880` (5MB) | Maximum size in bytes for external images | Optional |
| `IMAGE_PROXY_CACHE_TTL` | `2592000` (30 days) | Cache TTL in seconds for external images | Optional |
| `MESSAGE_TRUSTED_LINK_DOMAINS` | `[]` | Comma-separated list of hostnames whose external links open without the redirect confirmation modal. A lone `*` trusts every host (disables the modal). A leading `*.` wildcard also matches subdomains (`*.gouv.fr` matches `gouv.fr` and `impots.gouv.fr`); any other entry matches the host exactly (case-insensitive). Masked links (display text pointing to a different host than the real target) always prompt, even when their target is listed here. | Optional |

### Frontend

These settings are unset by default: an unset setting is omitted from the `/config` payload and the frontend then falls back on its deprecated `NEXT_PUBLIC_*` build-time variable (if any), then on its built-in default. Setting a value here always takes precedence over the frontend fallbacks.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `FRONTEND_SILENT_LOGIN_ENABLED` | `False` | Whether silent login is enabled | Optional |
| `FRONTEND_THEME_CONFIG` | None (frontend defaults to `{"theme": "white-label"}`) | Theme configuration served to the frontend (`theme`, `terms_of_service_url`, `footer`), as JSON | Optional |
| `FRONTEND_FORCED_DEFAULT_LANGUAGE` | None (frontend defaults to `False`) | When `True`, the frontend default language fallback is `LANGUAGE_CODE` instead of the browser language | Optional |
| `FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB` | None (frontend defaults to `100`) | Chunk size in MB for frontend multipart uploads | Optional |
| `FRONTEND_HELP_CENTER_URL` | None | Help center URL | Optional |
| `FRONTEND_FEEDBACK_WIDGET_CONFIG` | None | Feedback widget configuration (`api_url`, `path`, `channel`, `home_channel`), as JSON | Optional |
| `FRONTEND_LAGAUFRE_WIDGET_CONFIG` | None | Lagaufre widget configuration (`api_url`, `path`), as JSON | Optional |

Note: every language listed in `LANGUAGES` must have its translation files in the frontend (`/locales/*/xx-XX.json`), otherwise the UI falls back to `en-US`.

### Third-party Services

#### Drive

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `DRIVE_BASE_URL` | None | Base URL to access Drive endpoints | Optional |
| `DRIVE_APP_NAME` | `Drive` | Name of the Drive application used in the frontend | Optional |

### Identity Provider (Keycloak)

Used for provisioning-side operations against Keycloak (e.g. toggling
mandatory 2FA on a mail domain). Distinct from the OIDC login settings
above, which handle end-user authentication.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `IDENTITY_PROVIDER` | None | Identity-provider integration to enable (e.g. `keycloak`). Unset disables provisioning-side IdP calls. | Optional |
| `KEYCLOAK_URL` | None | Base URL of the Keycloak server. | Optional |
| `KEYCLOAK_REALM` | None | Keycloak realm. | Optional |
| `KEYCLOAK_CLIENT_ID` | None | Service-account client id used for admin/provisioning calls. | Optional |
| `KEYCLOAK_CLIENT_SECRET` | None | Service-account client secret. | Optional |
| `KEYCLOAK_GROUP_PATH_PREFIX` | None | Prefix for Keycloak group paths mapped to mail domains. | Optional |
| `KEYCLOAK_TOTP_ROLE_ID` | None | Realm role id assigned in Keycloak when "Mandatory 2FA" is enabled for a mailbox (see `FEATURE_MAILDOMAIN_MANAGE_TOTP`). | Optional |

### Domain DNS Provisioning

Hosting of MX/SPF/DKIM records for managed mail domains, optionally
automated through a DNS provider.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `MESSAGES_TECHNICAL_DOMAIN` | `localhost` | Technical domain that MX/SPF/DKIM records point at. | Optional |
| `MESSAGES_DNS_RECORDS` | MX/SPF/DKIM template | JSON template of expected DNS records, with `{technical_domain}` placeholders. | Optional |
| `DNS_DEFAULT_PROVIDER` | None | DNS provider used to auto-create records (e.g. `scaleway`). Unset = manual DNS. | Optional |
| `DNS_SCALEWAY_API_TOKEN` | None | Scaleway API token (when `DNS_DEFAULT_PROVIDER=scaleway`). | Optional |
| `DNS_SCALEWAY_PROJECT_ID` | None | Scaleway project id. | Optional |
| `DNS_SCALEWAY_TTL` | `3600` | TTL (seconds) for records created via Scaleway. | Optional |

### Calendar (CalDAV)

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `CALDAV_DEFAULT_URL` | None | Base URL of the external CalDAV server. | Optional |
| `CALDAV_DEFAULT_WEB_URL` | None | URL of the calendar web UI surfaced to the frontend. | Optional |
| `CALDAV_DEFAULT_PASSWORD` | None | Credential for the default CalDAV account. | Optional |

### Entitlements

Pluggable backend deciding what a user/domain is entitled to.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `ENTITLEMENTS_BACKEND` | `core.entitlements.backends.local.LocalEntitlementsBackend` | Dotted path to the entitlements backend class. | Optional |
| `ENTITLEMENTS_BACKEND_PARAMETERS` | `{}` | JSON parameters passed to the backend. | Optional |
| `ENTITLEMENTS_CACHE_TIMEOUT` | `300` | Cache TTL (seconds) for entitlement lookups. | Optional |

### Message Import

Tuning for the resumable message importer (EML / MBOX / PST files and IMAP;
see also `FEATURE_IMPORT_MESSAGES`). Imports are modelled as `type=import`
channels and resumed from a Redis watermark by `run_import_task`. See
[docs/imports.md](imports.md) for the design.

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `IMAP_TIMEOUT` | `60` | Socket timeout (seconds) for IMAP connections during import. | Optional |
| `IMAP_MAX_RETRIES` | `3` | Retry budget for transient IMAP failures during import. | Optional |
| `MESSAGES_IMPORT_STALL_TIMEOUT` | `900` | Seconds of heartbeat silence after which the scheduler treats a running import as crashed and re-dispatches it (which resumes from its watermark). Also the run-lock TTL. Must comfortably exceed the worst-case time between progress flushes. Continuous IMAP channels use `MESSAGES_IMPORT_IMAP_POLL_INTERVAL` as their clock instead. | Optional |
| `MESSAGES_IMPORT_IMAP_POLL_INTERVAL` | `900` | Poll cadence (seconds) for continuous IMAP imports: the scheduler re-dispatches each active continuous channel this often to pull new mail. Global (not settable per import); must be a positive integer. | Optional |
| `MESSAGES_IMPORT_MAX_FILE_SIZE` | `53687091200` | Largest archive (bytes) an import will accept (50 GiB); checked before a worker is spent on it. `0` disables the cap. | Optional |

### Spam Filtering

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `SPAM_CONFIG` | `{}` | JSON config for the spam checker. Empty `{}` disables it. Example: `{"rspamd_url": "http://mpa:8010/_api", "rspamd_auth": ""}`. | Optional |

### Celery / Task Queue

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `DISABLE_CELERY_BEAT_SCHEDULE` | `False` | Disable the periodic Beat schedule (search indexing, offload, selfcheck, …). | Optional |
| `CELERY_TASK_SEND_SENT_EVENT` | `True` | Emit Celery `task-sent` events (monitoring/Flower). | Optional |
| `CELERY_WORKER_SEND_TASK_EVENTS` | `True` | Workers emit task events (monitoring/Flower). | Optional |

### Metrics

| Variable | Default | Description | Required |
|----------|---------|-------------|----------|
| `METRICS_STORAGE_USED_OVERHEAD_BY_MESSAGE` | `1024` | Per-message overhead (bytes) added when computing reported storage usage. | Optional |

### Deprecated

_Set only to receive a startup deprecation warning; otherwise ignored._

| Variable | Default | Description | Required | ⚠️ Deprecated |
|----------|---------|-------------|----------|----------|
| `PROVISIONING_API_KEY` | None | Ignored since global `api_key` Channels landed. Migrate to a global `api_key` Channel. | Optional | Removed in a future release |
| `METRICS_API_KEY` | None | Ignored since global `api_key` Channels landed. Migrate to a global `api_key` Channel. | Optional | Removed in a future release |

## Legend

- **Required**: Must be set for the application to function
- **Dev**: Required for development/testing environments
- **Optional**: Has sensible defaults, can be customized

## Environment Files

The application uses environment files located in `deploy/env/` for different services:

- `backend.defaults` - Main Django application settings
- `common.defaults` - Shared settings across services
- `frontend.defaults` - Frontend configuration
- `postgresql.defaults` - PostgreSQL database configuration
- `keycloak.defaults` - Keycloak configuration
- `mta-in.defaults` - Inbound mail server settings
- `mta-out.defaults` - Outbound mail server settings
- `crowdin.defaults` - Translation service configuration

### Local Overrides

The `make bootstrap` command creates empty `.local` files for each service with a comment header:
```
# Put your local-specific, gitignored env vars here
```

These files are gitignored and allow for local development customizations without affecting the repository.

## Security Notes

⚠️ **Important Security Considerations:**

1. **Never commit actual secrets** - Use `.local` files only
2. **OIDC_ALLOW_DUPLICATE_EMAILS** - Should remain `False` in production
3. **CORS_ALLOW_ALL_ORIGINS** - Should be `False` in production
4. **DJANGO_SECRET_KEY** - Must be unique and secret in production
5. **Database passwords** - Use strong, unique passwords
6. **API tokens** - Rotate regularly and keep secure

## Production Deployment

For production deployments, ensure:

1. All **Required** variables are properly configured
2. Secrets are managed through secure secret management systems
3. HTTPS is enforced for all external communications
4. Database connections use SSL/TLS
5. File storage uses appropriate access controls

# Client Bridge (IMAP/SMTP)

The client bridge is an **optional** component that exposes Messages mailboxes to email clients (Thunderbird, mobile phones, etc.) via the legacy IMAP and SMTP protocols.

Messages is natively a modern, web-based messaging platform — closer in spirit to JMAP than to IMAP. The client bridge is a compatibility layer for users who prefer or need to access their mailbox from a traditional email client. Some Messages features (labels, real-time collaboration, rich threads) may not be fully available through IMAP.

## Architecture

The client bridge is a **standalone service** that communicates with the Messages backend exclusively over HTTP (via the REST API). It never accesses the database directly.

```text
┌──────────────┐       IMAP/SMTP        ┌───────────────────┐        HTTP         ┌──────────────┐
│ Email client │ ◄────────────────────► │ Client Bridge     │ ◄──────────────────► │   Messages   │
│ (Thunderbird)│                        │ (pymap/aiosmtpd)  │                      │   Backend    │
└──────────────┘                        └───────────────────┘                      └──────────────┘
```

It provides two protocol servers:

- **IMAP server** (based on [pymap](https://github.com/icgood/pymap)) — for reading messages and syncing flags
- **SMTP submission server** (based on [aiosmtpd](https://aiosmtpd.readthedocs.io/)) — for sending messages

### Deployment modes

Both servers run in a **single process** by default, but can be split:

| Mode | `ENABLE_IMAP` | `ENABLE_SMTP` | Use case |
|---|---|---|---|
| Combined (default) | `true` | `true` | Simple deployments |
| IMAP only | `true` | `false` | Read-only access, or separate SMTP process |
| SMTP only | `false` | `true` | Dedicated submission relay |

**Multiple client-bridge instances can connect to the same Messages backend.** The bridge is stateless — all data lives in the Messages API. This means you can scale horizontally or run separate IMAP and SMTP processes behind a load balancer.

### IMAP

Uses pymap's pluggable backend system with a custom `messages-api` backend that:

- **Authenticates** users via Channel app-specific passwords
- **Lists folders** mapped from message flags: INBOX, Sent, Drafts, Trash, Archive, Spam
- **Fetches messages** as raw EML from the Messages API
- **Syncs flags** back to the Messages API when users mark messages as read, starred, etc.

#### Virtual folders

Messages has no physical folder model — threads have boolean flags (`is_trashed`, `is_archived`, `is_spam`, etc.) and the web UI renders "folders" as filtered views. The IMAP bridge does the same: each IMAP folder is a virtual view backed by an API filter.

| IMAP folder | API filter | Notes |
|---|---|---|
| `INBOX` | Active threads (not trashed, archived, or spam) | Default view |
| `Sent` | Threads where the mailbox is the sender | |
| `Drafts` | Threads with a draft message | |
| `Trash` | `is_trashed = true` | |
| `Archive` | `is_archived = true` | |
| `Spam` | `is_spam = true` | |

**Moving messages** between folders works by toggling these flags via the API. For example, moving a message to Trash sets `is_trashed = true`; moving it back to Inbox clears that flag. Custom/arbitrary folders are not supported.

#### Limitations

IMAP is a legacy protocol with inherent limitations. The bridge maps Messages concepts to IMAP as faithfully as possible, but some features (e.g. labels, thread-level operations, real-time collaboration) are not representable in IMAP.

IMAP APPEND (uploading raw messages into a folder) is not yet supported. Email clients that try to save a copy of sent messages via APPEND will receive a `NO` response — this is harmless because the Messages backend already stores sent messages server-side during the SMTP submission flow.

### SMTP

Uses aiosmtpd with AUTH PLAIN/LOGIN to:

- **Authenticate** users via the same Channel credentials
- **Submit messages** to the Messages API for delivery

## Authentication

Two layers of authentication protect the client bridge:

1. **Service-level**: The bridge authenticates to the Messages API with a shared secret (`CLIENTBRIDGE_API_SECRET`), similar to how the MTA authenticates with `MDA_API_SECRET`.
2. **User-level**: Email clients authenticate with an app-specific password tied to a Channel.

To set up a channel:

1. Enable the feature on the backend:
   - `FEATURE_CLIENTBRIDGE=True` — enables client-bridge support (channel creation, auth endpoints)
   - `FEATURE_MAILBOX_ADMIN_CHANNELS=["client-bridge"]` — makes the channel type visible in the frontend Integrations UI (add `"client-bridge"` alongside any other enabled channel types)
2. A mailbox admin creates a Channel of type "Email client access" in the Integrations tab
3. An app-specific password is automatically generated and displayed once — save it immediately
4. Email clients connect with:
   - **Username**: the mailbox email address (e.g. `user@example.com`)
   - **Password**: the app-specific password

The password can be rotated from the channel settings if compromised. Rotation invalidates the previous password immediately.

### Roles

Each channel has a **role** (stored in `channel.settings["role"]`) that controls what the email client can do:

| Role | IMAP | SMTP | Use case |
|---|---|---|---|
| `reader` | Read-only (no flag changes) | No | Monitoring, archiving |
| `editor` | Read + write flags | No | Triage, flag management |
| `sender` (default) | Full access | Yes | Standard email client use |
| `sender_only` | No | Yes | Printers, apps, automated senders |

Roles are enforced at the API level, at IMAP login (rejects `sender_only`), at SMTP AUTH (rejects `reader` and `editor`), and in IMAP mailbox mode (`reader` gets read-only folders).

## TLS / SSL

App-specific passwords are sent over IMAP and SMTP during authentication. **TLS is strongly recommended in production** to protect credentials in transit.

### Option 1: TLS termination at a reverse proxy (recommended)

The simplest approach is to terminate TLS at a reverse proxy (HAProxy, Nginx, Traefik, etc.) and forward plaintext traffic to the client bridge on an internal network. This is the same pattern used for the Messages backend itself.

Example with HAProxy:

```haproxy
frontend imap-tls
    bind *:993 ssl crt /etc/ssl/certs/mail.pem
    default_backend client-bridge-imap

backend client-bridge-imap
    server bridge1 client-bridge:143

frontend smtp-tls
    bind *:465 ssl crt /etc/ssl/certs/mail.pem
    default_backend client-bridge-smtp

backend client-bridge-smtp
    server bridge1 client-bridge:587
```

Standard ports for email clients:
- **IMAP**: port `993` (implicit TLS / SSL)
- **SMTP submission**: port `465` (implicit TLS / SSL) or port `587` (STARTTLS)

### Option 2: Native TLS on the client bridge

Both pymap (IMAP) and aiosmtpd (SMTP) support TLS natively. Set the following environment variables to enable it:

| Variable | Description |
|---|---|
| `TLS_CERT` | Path to the TLS certificate file (PEM) |
| `TLS_KEY` | Path to the TLS private key file (PEM) |

When both are set, IMAP will serve with implicit TLS (typically on port 993) and SMTP will offer STARTTLS (on port 587) or implicit TLS (on port 465).

> **Note:** Native TLS support is not yet implemented in the server entrypoint. The `TLS_CERT` and `TLS_KEY` variables are reserved for future use. Use a reverse proxy for now.

### Enforcing TLS for SMTP AUTH

By default, the SMTP server allows authentication over plaintext connections (`auth_require_tls=False`). This is acceptable when TLS is terminated at a proxy, since the connection between the proxy and the bridge is on a trusted internal network. If the SMTP server is directly exposed to the internet with native TLS, set `auth_require_tls=True` in the aiosmtpd Controller configuration to reject AUTH commands on unencrypted connections.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `MESSAGES_API_BASE_URL` | Base URL for the Messages API | (required) |
| `CLIENTBRIDGE_API_SECRET` | Shared secret for service-to-service auth | (required) |
| `ENABLE_IMAP` | Enable IMAP server | `true` |
| `IMAP_HOST` | IMAP server bind host | `0.0.0.0` |
| `IMAP_PORT` | IMAP server bind port | `143` |
| `ENABLE_SMTP` | Enable SMTP server | `true` |
| `SMTP_HOST` | SMTP server bind host | `0.0.0.0` |
| `SMTP_PORT` | SMTP server bind port | `587` |

## Development

From the repository root:

```bash
# Run tests
make test-client-bridge

# Lint
make lint-client-bridge

# Regenerate lock file after changing dependencies
make deps-lock-client-bridge
```

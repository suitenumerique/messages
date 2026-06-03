# Outbound Webhooks

For every inbound message Messages can `POST` a notification to an HTTP
endpoint of your choosing. This page documents the on-the-wire format so
receivers can be implemented against a stable contract.

Webhooks are **outbound** — Messages calls out to your endpoint. Anything
**inbound** (third parties calling into Messages) goes through other
channel types (`api_key`, `widget`, `mta`, etc.) and is not the subject
of this document.

## When does it fire?

For each inbound message accepted by the MTA the delivery pipeline goes
through two webhook phases:

1. **`before_spam`** — fires right after the message has been parsed,
   before any spam check. `is_spam` is not yet known.
2. **`after_spam`** — fires after the spam verdict but before the
   `Message` row is created. `is_spam` is known.

Each webhook channel picks **one** phase via `settings.phase`
(default `after_spam`).

A channel may also be **blocking** (`settings.blocking: true`). A
blocking webhook gets to shape delivery: it can drop the message, ask
to be retried later, or return a small JSON body that overrides the
spam verdict and/or attaches labels to the resulting thread (see
[Response contract](#response-contract) below). Non-blocking webhooks
are fire-and-forget — failures are logged and the pipeline continues
unchanged.

## Channel scopes

A webhook channel can be configured at three scopes:

| `scope_level` | Fires on                                | How to create                                |
| ------------- | --------------------------------------- | -------------------------------------------- |
| `mailbox`     | Messages delivered to that mailbox      | Mailbox admin via the **Integrations** modal |
| `maildomain`  | Messages delivered to any mailbox of the domain | Maildomain admin via API / admin       |
| `global`      | Every message on the instance           | Superuser via the Django admin or CLI        |

A given inbound message fans out to every matching channel.
`global` is intentionally not creatable through the public REST API —
it's a sensitive instance-wide hook.

## Configuration

A webhook channel stores its configuration in `Channel.settings`
(a JSON dict):

```json
{
  "url":         "https://example.com/inbox-hook",
  "events":      ["message.received"],
  "phase":       "after_spam",
  "format":      "eml",
  "blocking":    false,
  "auth_method": "jwt"
}
```

| Key           | Type     | Default        | Description                                                                 |
| ------------- | -------- | -------------- | --------------------------------------------------------------------------- |
| `url`         | string   | **required**   | `http://` or `https://` endpoint. Validated by the SSRF guard at each call. |
| `events`      | string[] | **required**   | Currently only `message.received` is implemented.                           |
| `phase`       | string   | `after_spam`   | `before_spam` or `after_spam`.                                              |
| `format`      | string   | `eml`          | `eml`, `jmap`, or `jmap_without_body` (see [Payload formats](#payload-formats)). |
| `blocking`    | bool     | `false`        | If true, the webhook response determines delivery (see [Response contract](#response-contract) below). |
| `auth_method` | string   | **required**   | `jwt` or `api_key` (see [Authentication](#authentication)).                 |

The serializer validates every change to `settings`, on create **and**
on settings-only PATCH — there is no path that lets a malformed value
slip onto an existing channel.

## HTTP request shape

Every call is:

* `POST` to `settings.url`.
* `User-Agent: Messages-Webhook/1.0`.
* 30-second timeout.
* HTTP `3xx` is **not** followed — receivers must respond on the URL
  configured, not after a redirect.
* The destination hostname/IP must pass the shared SSRF check (no
  loopback, link-local, private, multicast, reserved, or cloud metadata
  addresses; no IP literals).

### Authentication

Every webhook channel has **one root secret**, minted server-side,
returned exactly once at create time and rotatable via
`POST /channels/{id}/regenerate-secret/`. The `auth_method`
setting picks how that root is presented on each POST. The root itself
never travels on the wire.

| `auth_method` | Headers sent                                                                                                | Wire value                                                | Receiver verifies                                                              |
| ------------- | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `jwt`         | `X-StMsg-Webhook-Timestamp`, `X-StMsg-Webhook-Signature: v1=<hex>`, `Authorization: Bearer <HS256 JWT>`     | HMAC sig + JWT, both **keyed** by the root                | HMAC-SHA256 of `f"{timestamp}.{body}"` with the root, **or** the HS256 JWT.    |
| `api_key`     | `X-StMsg-Api-Key: <whk_…>`                                                                                  | `whk_` + `HMAC-SHA256(root, "messages.webhook.api_key.v1").hex()`  | Constant-time compare of the header against the receiver's stored copy.        |

A channel sends **only** the headers for its configured method — the
unused presentation never rides on the wire, so it can't leak through
receiver-side proxies or debug panes. The API-key value is a
**one-way derivation** of the root, so a receiver-side leak of the API
key reveals nothing about the root: HMAC/JWT verification on other
receivers stays unforgeable.

#### Picking a method

- `jwt` — best when the receiver controls a server (n8n, your own
  Lambda, a Flask/Express app, Cloudflare Worker). Body integrity is
  proven by the HMAC; JWT lets receivers verify with stock libraries.
- `api_key` — for low-code receivers that can only check a header
  (Zapier "API key in header" trigger, IFTTT, a Zap webhook step).

#### Switching methods on an existing channel

PATCH the channel's `settings.auth_method`. The root secret is **not**
rotated — only the wire presentation changes — but the receiver was
given the old method's credential at creation. To get the new method's
credential, call `POST /channels/{id}/regenerate-secret/`: the
response returns either `webhook_secret` (jwt) or `webhook_api_key`
(api_key), matching the channel's current method. Rotation invalidates
the previous credential, so update the receiver before the next inbound
message lands.

### Envelope headers (always set, regardless of format)

| Header                | Value                                                            |
| --------------------- | ---------------------------------------------------------------- |
| `Content-Type`        | `message/rfc822` for `eml`, `application/json` for both JMAP variants |
| `X-StMsg-Event`       | `message.received`                                               |
| `X-StMsg-Phase`       | `before_spam` or `after_spam`                                    |
| `X-StMsg-Channel-Id`  | UUID of the firing webhook Channel                               |
| `X-StMsg-Mailbox`     | Destination mailbox address                                      |
| `X-StMsg-Recipient`   | Envelope `RCPT TO` (usually the same as `X-StMsg-Mailbox`)       |
| `X-StMsg-Is-Spam`     | `true`, `false`, or `unknown` (`unknown` in the `before_spam` phase) |
| `X-StMsg-Message-Id`  | Original `Message-ID` header value (angle-bracketed), if any     |

### Response contract

The classification below applies to **blocking** webhooks. Non-blocking
webhooks treat every outcome as success — their bodies are ignored.

| Outcome                               | Decision   | What happens                                                                    |
| ------------------------------------- | ---------- | ------------------------------------------------------------------------------- |
| HTTP `2xx`, empty / non-JSON body     | CONTINUE   | Delivery proceeds normally.                                                     |
| HTTP `2xx` + JSON action body         | see below  | Body parsed for `action` / `is_spam` / `labels`.                                |
| HTTP `4xx`                            | DROP       | Receiver definitively rejected the message; `InboundMessage` deleted.           |
| HTTP `408`, `429`, `5xx`              | RETRY      | Transient — `InboundMessage` kept; the 5-min sweep re-fires the webhook.        |
| Connection error, timeout, DNS, etc.  | RETRY      | Transient.                                                                      |
| SSRF rejection                        | DROP       | Config error on our side — retrying won't help.                                 |
| Missing signing secret (misconfig)    | DROP       | The dispatcher fails closed rather than POST an unsigned request.               |

`RETRY` is bounded: an `InboundMessage` held in retry for more than
**7 days** is dropped with a loud `ERROR` log. This prevents a
permanently-broken receiver from pinning a row forever. (When you fix
the receiver within 7 days, the next sweep delivers normally.)

#### JSON action body

When a blocking webhook returns `HTTP 2xx` with `Content-Type:
application/json`, the body MAY contain the following keys. All are
optional; unknown keys are ignored.

```json
{
  "action":         "drop",
  "is_spam":        true,
  "labels":         ["b3c9c1c3-1f4a-4d4a-9b2d-9c5a2a7c0a01"],
  "assign_to":      ["alice@example.org"],
  "mark_starred":   true,
  "mark_read":      true,
  "mark_trashed":   false,
  "mark_archived":  true,
  "skip_autoreply": true,
  "add_event": [
    {"type": "im", "content": "AI summary: budget Q4 update"}
  ],
  "reply_draft":    {"template": "b3c9c1c3-1f4a-4d4a-9b2d-9c5a2a7c0a01"}
}
```

| Key              | Type           | Meaning                                                                                                          |
| ---------------- | -------------- | ---------------------------------------------------------------------------------------------------------------- |
| `action`         | `"drop"` / `"retry"` | `"drop"` drops the message at this phase; `"retry"` re-queues the inbound task (bounded by the 7-day retry budget). Any other value (or omission) is treated as accept. Case-insensitive. |
| `is_spam`        | bool           | Override the spam verdict. Acts as a full antispam: in the `before_spam` phase this **skips rspamd**.            |
| `labels`         | string[]       | UUIDs of `Label` rows in the destination mailbox to attach to the thread once it is created.                     |
| `assign_to`      | string[]       | OIDC emails of users to assign to the resulting thread (one `ThreadEvent ASSIGN` per webhook, channel-attributed). |
|  `mark_starred`  | bool (true only) | Star the resulting thread for the destination mailbox.                                                         |
| `mark_read`      | bool (true only) | Mark the resulting thread as read for the destination mailbox.                                                 |
| `mark_trashed`   | bool (true only) | Land the message with `is_trashed=true`. (Distinct from `action: "drop"` — the row stays, just hidden.)        |
| `mark_archived`  | bool (true only) | Land the message with `is_archived=true`.                                                                      |
| `skip_autoreply` | bool (true only) | Suppress the standard autoreply for this message (in addition to the `is_spam=true` suppression).              |
| `add_event`      | object[]       | Persist one `ThreadEvent` per entry, attributed to this webhook channel. See [Events](#add_event-events).        |
| `reply_draft`    | object         | `{template: "<MessageTemplate UUID>"}` — materialise a draft reply for the user to refine + send. See [Reply drafts](#reply_draft-drafts). |

Notes:

* `action: "drop"` always wins. Setting `action: "drop"` together with
  `labels` or `assign_to` still drops — the thread is never created,
  so neither side effect is applied.
* `is_spam` discriminates between **explicit false (ham)** and **no
  opinion**: returning `{}` leaves the dispatcher's verdict (typically
  rspamd) untouched, while returning `{"is_spam": false}` forces ham.
* `labels` only makes sense for **mailbox-scoped** channels: labels are
  per-mailbox. For domain- or global-scoped channels the UUIDs are
  validated against the receiving mailbox; unknown UUIDs are logged and
  skipped, not raised — a misbehaving webhook must not stall delivery.
* `assign_to` resolves each email to a User row with
  `email__iexact`. The resolution is **strict but quiet**: emails that
  resolve to zero users, to multiple users (ambiguous — `User.email`
  isn't unique, see `MAILBOX_ROLES_CAN_BE_ASSIGNED`), or to a user
  whose mailbox role isn't one of `EDITOR` / `SENDER` / `ADMIN` are
  logged and skipped. **No auto-create**: a webhook receiver cannot
  mint a User row. Each blocking webhook that contributes assignees
  produces its own `ThreadEvent` with `channel` set to that webhook's
  channel, so the audit timeline keeps per-receiver attribution. The
  resulting `ThreadEvent.author` is `null` (the receiver is not a
  user); the existing partial UniqueConstraint on `UserEvent(user,
  thread) WHERE type=assign` makes duplicate asks idempotent.
* Bool flags (`mark_starred` / `mark_read` / `mark_trashed` / `mark_archived` /
  `skip_autoreply`) use **`true`-only semantics**: a receiver opting in
  with `true` flips the flag; `false`, missing, or non-bool values
  are "no opinion". The multi-webhook merge is therefore a simple OR
  — a later receiver can't silently veto an earlier receiver's
  directive. `mark_trashed` / `mark_archived` set the corresponding
  field on the `Message` row at creation time; `mark_starred` / `mark_read`
  set `starred_at` / `read_at` on the destination `ThreadAccess` (no-
  op when already set, so re-firing doesn't reset them).

#### `add_event` events

`add_event` is a list of structured events to persist on the resulting
thread. Each entry becomes one `ThreadEvent`, attributed to the
firing webhook via the `channel` FK; `author` is `null`.

Supported types:

| `type` | Required fields    | Effect                                                                              |
| ------ | ------------------ | ----------------------------------------------------------------------------------- |
| `"im"` | `content` (string) | Persists as an internal-message ThreadEvent — the same surface humans post into.    |

Unknown types are silently skipped at the classifier — the contract
stays forward-compatible so receivers can begin emitting new types
(e.g. `"iframe"`) before the server learns them, with no churn for
the receivers that already work.

#### `reply_draft` drafts

`reply_draft: {"template": "<UUID>"}` materialises a **draft reply**
to the incoming message, pre-filled from a `MessageTemplate`. The
draft is threaded under the inbound message, ``Re:``-prefixed, and
addressed to the original sender — the user reviews and refines it
in the UI, then sends with a click. **We do not auto-send.**

Implementation reuses the autoreply pipeline (sender contact, subject
prefix, message + recipient creation, signature resolution); the only
difference is the body lands in `draft_blob` (the rich-text editor's
JSON shape, from the template's `raw_body`), not in `blob`. The
editor round-trip is therefore identical to a hand-composed draft.

Validation:

* The template must be `type=message` and `is_active=true`, scoped to
  the **destination mailbox or its maildomain**. Templates from other
  mailboxes / domains are silently skipped — a webhook receiver
  cannot draft from arbitrary templates.
* Templates from outside the destination scope are silently skipped
  (logged, not raised).
* If the inbound message has no sender we can reply to, the draft is
  skipped (same rule the autoreply path uses).

Each blocking webhook that asks produces **one draft** attributed to
its own channel (`Message.channel` FK preserved for audit). If two
webhooks each ask, the user sees two drafts — they pick which one to
send, or delete both.

#### Multi-webhook merge

When several blocking webhooks fire on the same phase, their outcomes
merge deterministically:

* **decision**: most severe wins (`DROP` > `RETRY` > `CONTINUE`). The
  dispatcher short-circuits the fan-out as soon as any webhook drops.
* **is_spam**: last decisive value wins (DB iteration order).
* **labels**: set union across all webhooks.
* **assign_to**: each webhook's list lands as its own ThreadEvent
  (channel attribution preserved). A user assigned by an earlier
  webhook is absorbed by the partial UniqueConstraint when a later
  webhook re-asks — no duplicate UserEvent, the first ask is the
  canonical attribution.
* **mark_starred / mark_read / mark_trashed / mark_archived / skip_autoreply**:
  OR-merged — any `true` wins.
* **add_event**: each entry lands as its own ThreadEvent, in the
  order webhooks fired. No deduplication.
* **reply_draft**: each blocking webhook that asks produces one draft
  Message, attributed to its own channel. No deduplication — multiple
  receivers each asking yield multiple drafts.

## Payload formats

The three formats are mutually exclusive — pick one per channel. The
envelope headers above are identical across formats.

### `eml` (default)

The request body is the **raw RFC-822 message bytes**, exactly as the
MTA received them.

```http
POST /inbox-hook HTTP/1.1
Content-Type: message/rfc822
X-StMsg-Event: message.received
X-StMsg-Phase: after_spam
X-StMsg-Channel-Id: 05f1f991-c2e9-4fa7-8a78-98c3aa904c7c
X-StMsg-Mailbox: alice@example.com
X-StMsg-Recipient: alice@example.com
X-StMsg-Is-Spam: false
X-StMsg-Message-Id: <abc123@example.org>

From: Bob <bob@example.org>
To: alice@example.com
Subject: Hi
Message-ID: <abc123@example.org>
Content-Type: text/plain; charset=utf-8

Hello, Alice!
```

This is the simplest format. Any email library can parse it
(`email.message_from_bytes` in Python, JavaMail's `MimeMessage`,
mailparser in Node, etc.).

### `jmap`

The request body is a **strictly JMAP-compliant `Email` object** per
[RFC 8621 §4.1][rfc8621] serialised as JSON. The body is the object
itself — there is **no surrounding envelope** in the JSON; envelope
metadata lives in the headers above.

```json
{
  "messageId":  ["abc123@example.org"],
  "inReplyTo":  [],
  "references": [],
  "from":       [{"email": "bob@example.org", "name": "Bob"}],
  "to":         [{"email": "alice@example.com", "name": ""}],
  "cc":         null,
  "bcc":        null,
  "sender":     null,
  "replyTo":    null,
  "subject":    "Hi",
  "sentAt":     "2026-01-01T12:00:00Z",
  "receivedAt": "2026-06-01T08:43:21Z",
  "headers": [
    {"name": "from",    "value": "Bob <bob@example.org>"},
    {"name": "to",      "value": "alice@example.com"},
    {"name": "subject", "value": "Hi"}
  ],
  "bodyValues": {
    "1": {"value": "Hello, Alice!", "isEncodingProblem": false, "isTruncated": false}
  },
  "textBody": [
    {"partId": "1", "blobId": null, "size": 13, "name": null, "type": "text/plain", "charset": "utf-8", "disposition": null, "cid": null, "language": null, "location": null}
  ],
  "htmlBody":    [],
  "attachments": [],
  "hasAttachment": false,
  "preview": null
}
```

#### Fields omitted on purpose

JMAP defines a few `Email` properties that only make sense once the
message is **stored** in a JMAP server. The webhook fires *before* the
`Message` row exists (and may abort delivery in the blocking case), so
these are intentionally absent:

* `id`, `blobId`, `threadId`, `mailboxIds`, `keywords`.

Attachment **bytes** are also intentionally omitted: JMAP keeps
attachment content behind a `blobId` and a separate fetch, which has no
analogue in a fire-and-forget webhook. The `attachments[]` entries
still describe each attachment's `type`, `size`, `name`, `disposition`
and `cid`. If you need the raw bytes pick `format: "eml"` instead.

#### Date formatting

`sentAt` and `receivedAt` are JMAP `UTCDate` strings: ISO-8601 in UTC
with an explicit `Z` suffix, e.g. `2026-01-01T12:00:00Z` (not
`+00:00`). This matches RFC 8621 §1.4.

### `jmap_without_body`

Same JMAP `Email` shape as `jmap`, but the body content and attachments
are dropped:

* `textBody`, `htmlBody`, `bodyValues`, `attachments` are **omitted**.
* `hasAttachment` is preserved as a single boolean so receivers can
  still tell whether the original message had attachments.
* All envelope fields (`from`, `to`, `subject`, `messageId`, `headers`,
  `sentAt`, `receivedAt`, …) are included.

Use this format when you only need the "a message arrived" signal plus
addressing metadata — for example to forward to a chat channel — and
don't want the body content to leave the instance over the wire.

## Example receiver

A minimal Python receiver that accepts both formats:

```python
import email
import json
from flask import Flask, request

app = Flask(__name__)

@app.post("/inbox-hook")
def inbox_hook():
    content_type = request.headers.get("Content-Type", "")
    if content_type.startswith("message/rfc822"):
        msg = email.message_from_bytes(request.get_data())
        print("EML subject:", msg["subject"])
    elif content_type.startswith("application/json"):
        body = request.get_json()
        print("JMAP subject:", body["subject"])
        # Body content may not be there in jmap_without_body mode.
        body_values = body.get("bodyValues") or {}
        for part_id, value in body_values.items():
            print(f"  part {part_id}: {value['value'][:80]}")
    else:
        return "unsupported", 415

    # Echo envelope metadata for logging.
    print("phase:",   request.headers["X-StMsg-Phase"])
    print("is_spam:", request.headers["X-StMsg-Is-Spam"])
    print("mailbox:", request.headers["X-StMsg-Mailbox"])
    return "", 200
```

## Security notes

* The endpoint URL is **caller-controlled** (a mailbox admin sets it),
  so every call goes through the shared `SSRFSafeSession`:
  * Only `http://` and `https://` URLs are accepted.
  * IP literals are rejected — a domain name is required.
  * Hostnames resolving to loopback, link-local, private, multicast,
    reserved, or cloud-metadata addresses are rejected.
  * The validated IP is **pinned** for the actual connection, defeating
    DNS-rebinding (TOCTOU). For HTTPS the TLS certificate is verified
    against the original hostname.
* Blocking webhooks are silent for the original sender — the inbound
  SMTP transaction has already been accepted. A blocking-drop is
  visible only through logs and the pipeline's `dropped_by_webhook`
  return value.

[rfc8621]: https://www.rfc-editor.org/rfc/rfc8621#section-4.1

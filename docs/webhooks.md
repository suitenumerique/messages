# Outbound Webhooks

For every inbound message Messages can `POST` a notification to an HTTP
endpoint of your choosing. This page documents the on-the-wire format so
receivers can be implemented against a stable contract.

Webhooks are **outbound** — Messages calls out to your endpoint. Anything
**inbound** (third parties calling into Messages) goes through other
channel types (`api_key`, `widget`, `mta`, etc.) and is not the subject
of this document.

## When does it fire?

A webhook channel has a single **`trigger`**: the point in a message's
lifecycle that fires it. The event name says both *when* it fires and
*whether* it can influence delivery — there's no separate blocking flag,
so invalid combinations can't be expressed:

| `trigger`             | Fires                                              | Blocking? | `is_spam` |
| --------------------- | ------------------------------------------------- | --------- | --------- |
| `message.inbound`     | The message just arrived, before the spam check   | yes (sync)  | pending |
| `message.delivering`  | After the spam verdict, while delivery is in flight | yes (sync) | known  |
| `message.delivered`   | After the message has landed in the mailbox       | no (async)  | final   |

Future lifecycle events (e.g. `message.sent`) are added as new
`trigger` values.

**`message.inbound`** and **`message.delivering`** are *synchronous*: they
run **inline** on the pipeline worker and get to shape delivery — drop the
message, or return a small JSON body that overrides the spam verdict
and/or attaches labels to the resulting thread (see
[Response contract](#response-contract)). They can't *ask* to be retried
through the body: a webhook that needs the message redelivered returns a
non-2xx status, which is treated as a transient failure → RETRY.

**`message.delivered`** is *asynchronous* — fire-and-forget; failures are
logged and the pipeline continues unchanged. Because it can't influence
delivery, it doesn't run on the pipeline worker at all: the channel is
**recorded** during the pipeline (capturing the final `is_spam`) and the
actual POST is handed to a background task **after the `Message` is
persisted**. The task renders the body from the stored message, so
nothing is copied or sent through the broker. Two consequences:

* It fires only for messages that become a `Message` — not for one a
  blocking webhook later **drops**. (Spam is *not* a drop: it still lands
  in the spam folder, so it still fires, with `X-StMsg-Is-Spam: true`.)
* It always runs after the spam step, so its `X-StMsg-Is-Spam` is the
  final verdict.

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
  "trigger":     "message.delivered",
  "format":      "eml",
  "auth_method": "jwt"
}
```

| Key           | Type     | Default        | Description                                                                 |
| ------------- | -------- | -------------- | --------------------------------------------------------------------------- |
| `url`         | string   | **required**   | `https://` endpoint. **Rejected at create/update** if it resolves to an internal address or doesn't resolve, and re-validated by the SSRF guard (with IP pinning) at each call. `http://` is accepted only when Django `DEBUG` is on (the local-dev escape hatch). |
| `trigger`     | string   | **required**   | `message.inbound`, `message.delivering`, or `message.delivered` (see [When does it fire?](#when-does-it-fire)). |
| `format`      | string   | `eml`          | `eml`, `jmap`, or `jmap_metadata` (see [Payload formats](#payload-formats)). |
| `auth_method` | string   | **required**   | `jwt` or `api_key` (see [Authentication](#authentication)).                 |

The serializer validates every change to `settings`, on create **and**
on settings-only PATCH — there is no path that lets a malformed value
slip onto an existing channel.

## HTTP request shape

Every call is:

* `POST` to `settings.url`.
* `User-Agent: Messages-Webhook/1.0`.
* 30-second timeout.
* HTTP `3xx` **is** followed (small hop limit), but **every hop is
  re-validated and re-pinned** by the SSRF guard and the `POST` is
  re-issued (method + body preserved), so a receiver behind a load
  balancer or URL canonicaliser still gets the signed payload — and a
  redirect can't point the delivery at an internal target.
* The destination hostname/IP must pass the shared SSRF check (no
  loopback, link-local, private, multicast, reserved, or cloud metadata
  addresses; no IP literals).

### Authentication

Every webhook channel has **one root secret**, minted server-side,
returned exactly once at create time and rotatable by POSTing to the
channel's `regenerate-secret/` action. That action's path prefix
depends on the channel's scope: a mailbox-scoped channel is reached via
the mailbox route as `POST
/mailboxes/{mailbox_id}/channels/{id}/regenerate-secret/`, while a
caller's own channels are at `POST
/users/me/channels/{id}/regenerate-secret/`. The `auth_method`
setting picks how that root is presented on each POST. The root itself
never travels on the wire.

| `auth_method` | Headers sent                                       | Wire value                                                        | Receiver verifies                                                                              |
| ------------- | -------------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `jwt`         | `Authorization: Bearer <HS256 JWT>`                | HS256 JWT **keyed** by the root                                   | Verify the JWT with the root using any JWT library; it binds the exact body via the `body_sha256` claim and expires (`exp`, 5 min) with a `jti` nonce. |
| `api_key`     | `Authorization: Bearer <whk_…>`                    | `whk_` + `HMAC-SHA256(root, "messages.webhook.api_key.v1").hex()`  | Constant-time compare of the Bearer token against the receiver's stored copy (an opaque static key, not a JWT). |

A channel sends **only** the headers for its configured method — the
unused presentation never rides on the wire, so it can't leak through
receiver-side proxies or debug panes. The API-key value is a
**one-way derivation** of the root, so a receiver-side leak of the API
key reveals nothing about the root: JWT verification (and the API-key
derivation) on other receivers stays unforgeable.

#### Picking a method

- `jwt` — best when the receiver controls a server (n8n, your own
  Lambda, a Flask/Express app, Cloudflare Worker). Verify with stock JWT
  libraries; the token binds the exact body (`body_sha256`) and expires.
- `api_key` — for low-code receivers that can only do a static
  header-equals-value check (Zapier, IFTTT, a Zap webhook step): they just
  compare `Authorization` against the stored `Bearer <whk_…>` value.

#### Switching methods on an existing channel

PATCH the channel's `settings.auth_method`. The root secret is **not**
rotated — only the wire presentation changes — but the receiver was
given the old method's credential at creation. To get the new method's
credential, call the channel's `regenerate-secret/` action (scoped path
as above): the
response returns either `secret` (jwt) or `api_key` (api_key),
matching the channel's current method. Rotation invalidates
the previous credential, so update the receiver before the next inbound
message lands.

### Envelope headers (always set, regardless of format)

| Header                | Value                                                            |
| --------------------- | ---------------------------------------------------------------- |
| `Content-Type`        | `message/rfc822` for `eml`, `application/json` for both JMAP variants |
| `X-StMsg-Trigger`     | The lifecycle event that fired (`message.inbound` / `message.delivering` / `message.delivered`). Route on this — it says what happened and, implicitly, whether the webhook blocked. |
| `X-StMsg-Instance`    | Public base URL of the originating instance (e.g. `https://messages-public-url.example.com`). **Only present when the instance sets `INSTANCE_URL`.** Combine with the `*-Id` headers to build callback API URLs. |
| `X-StMsg-Channel-Id`  | UUID of the firing webhook Channel                               |
| `X-StMsg-Mailbox`     | Destination mailbox address                                      |
| `X-StMsg-Mailbox-Id`  | UUID of the destination `Mailbox` (the API is keyed by this, not the address) |
| `X-StMsg-Recipient`   | Envelope `RCPT TO` (usually the same as `X-StMsg-Mailbox`)       |
| `X-StMsg-Is-Spam`     | `true`, `false`, or `pending` (`pending` for `message.inbound`, which fires before the spam check) |
| `X-StMsg-Message-Id`  | UUID of the stored `Message` — **non-blocking only** (see note)   |
| `X-StMsg-Thread-Id`   | UUID of the `Message`'s `Thread` — **non-blocking only**          |

The MIME message-id is **not** sent as a header — every body format
already carries it (`messageId` in the JMAP variants, the raw
`Message-ID:` header in `eml`).

`X-StMsg-Mailbox-Id`, `X-StMsg-Message-Id` and `X-StMsg-Thread-Id` are the
platform's own ids (the API is keyed by UUID, not by address). To call back
into the API, join them to the instance base URL — sent as `X-StMsg-Instance`
when configured — e.g.
`GET {X-StMsg-Instance}/api/v1.0/mailboxes/{X-StMsg-Mailbox-Id}/…`.
`X-StMsg-Message-Id` / `X-StMsg-Thread-Id` are only present on **non-blocking**
webhooks, which fire after the `Message` is persisted; blocking webhooks
run before it exists, so they can't carry them.

### Response contract

The classification below applies to **blocking** webhooks. Non-blocking
webhooks treat every outcome as success — their bodies are ignored.

The three possible **decisions** are about the inbound email itself:

* **CONTINUE** — deliver it: the `Message` and its thread are created
  normally.
* **DROP** — discard it: **no `Message` and no thread are created**, so
  the recipient never sees the email. The original sender is *not*
  notified (the inbound SMTP transaction was already accepted). The
  short-lived internal `InboundMessage` processing row is also removed —
  but that happens on *every* terminal outcome, including normal
  delivery, so "the `InboundMessage` is deleted" is not what makes DROP
  special; **the email never landing** is.
* **RETRY** — keep the email queued and re-fire the webhook on the next
  5-minute sweep (bounded by the **deferral window** below).

**A webhook error never drops the email.** The *only* way a blocking
webhook discards a message is by **explicitly** returning
`{"action": "drop"}` with an HTTP `2xx` (see
[JSON action body](#json-action-body)). Other failures split two ways:

* A **transient** failure — a `4xx`/`5xx`, a timeout, a connection error,
  a receiver that is temporarily down — is held for **RETRY** and re-fired
  by the sweep until it recovers (bounded by the deferral window).
* A **config** failure that waiting cannot fix — the URL is refused by the
  [SSRF guard](#security-notes) (resolves to an internal address, or won't
  resolve), or the channel is missing its secret / url / auth_method —
  delivers the mail **past** the broken webhook (**CONTINUE**) and logs an
  `ERROR` so an admin fixes or disables the channel. We never POST the
  internal target (the guard already blocked it); we just don't stall a
  whole scope's inbound — instance-wide for a GLOBAL blocking webhook — for
  48h on a config that has never worked. Create/update validation rejects
  internal/unresolvable URLs up front, so at dispatch this is only a DNS
  rebinding or a hand-edited row.

Either way the user never loses mail. Note the config path is **fail-open**
for that one webhook: a hard spam/security gate is bypassed during the
failure (the rest of the pipeline, incl. rspamd, still runs) — a conscious
trade against stalling all inbound; the `ERROR` log is your signal to fix it.

| Outcome                                         | Decision        | What happens                                                                 |
| ----------------------------------------------- | --------------- | ---------------------------------------------------------------------------- |
| HTTP `2xx`, empty / non-JSON body               | CONTINUE        | Email delivered normally.                                                    |
| HTTP `2xx` + `{"action": "drop"}`               | DROP            | The **only** path to DROP — the receiver deliberately discards the email.    |
| HTTP `2xx` + other JSON action body             | see below       | Body parsed for `action` / `is_spam` / `add_labels`; default is CONTINUE.    |
| Any non-2xx (`4xx`/`5xx`/`3xx`), timeout, conn. | RETRY           | Transient — held, re-fired by the sweep, bounded by the deferral window.   |
| SSRF rejection (internal / unresolvable URL)    | CONTINUE + alert | Config error retry can't fix; deliver past it, `log.error`. URL never dialed.|
| Missing secret / url / auth_method (misconfig)  | CONTINUE + alert | Non-DRF misconfig retry can't fix; deliver past it, `log.error`.             |

`RETRY` is bounded by a **deferral window** so a persistently-failing
processing step can neither pin a row forever nor lose mail. If a step
is still failing **48 hours** after the message arrived, we stop holding
and **deliver the message anyway** — landed in the inbox (`is_spam=False`
so it isn't buried) and stamped with an `X-StMsg-Processing-Failed`
marker. The web UI reads that marker and shows a prominent warning banner
(the same surface as the unverified-sender warning), so the recipient
knows the message bypassed a processing step and can review it with
caution. Nothing is ever silently dropped; if the step recovers within
the window, the next sweep delivers normally with no marker. (That
`X-StMsg-Processing-Failed` marker rides in the stored MIME as an
`X-StMsg-*` header; sender-supplied `X-StMsg-*` headers are stripped at
ingest, so a malicious **sender** can't forge it to fake a bypassed check.)

The mechanism is generic: a blocking webhook is the trigger today, but
any step that returns `RETRY` (e.g. a persistently-unreachable spam
checker) is deferred the same way.

> **Delivery is at-least-once — make your receiver idempotent.** A
> `RETRY` re-fires the webhook on the next sweep, and a worker crash
> after we POST but before we record success can re-deliver the same
> message. The `Message` itself is created exactly once (deduplicated by
> `Message-ID`), but your endpoint may legitimately see the *same*
> message more than once. Key on the `Message-ID` (or the
> `X-StMsg-*` envelope headers) and treat repeats as no-ops.

#### JSON action body

When a blocking webhook returns `HTTP 2xx` with `Content-Type:
application/json`, the body MAY contain the following keys. All are
optional; unknown keys are ignored.

```json
{
  "action":         "drop",
  "is_spam":        true,
  "add_labels":     ["b3c9c1c3-1f4a-4d4a-9b2d-9c5a2a7c0a01"],
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
| `action`         | `"drop"`       | `"drop"` drops the message at this phase. Any other value (or omission) is treated as accept. Case-insensitive. There is no body-driven `"retry"`: a 2xx is a successful response. If you need the message redelivered later, return a non-2xx status (e.g. `429`/`503`) — it is held for retry, bounded by the 48h deferral window. |
| `is_spam`        | bool           | Override the spam verdict. Acts as a full antispam: for a `message.inbound` webhook this **skips rspamd**. |
| `add_labels`     | string[]       | UUIDs of `Label` rows in the destination mailbox to attach to the thread once it is created.                     |
| `assign_to`      | string[]       | OIDC emails of users to assign to the resulting thread (one `ThreadEvent ASSIGN` per webhook, channel-attributed). |
| `mark_starred`   | bool (true only) | Star the resulting thread for the destination mailbox.                                                         |
| `mark_read`      | bool (true only) | Mark the resulting thread as read for the destination mailbox.                                                 |
| `mark_trashed`   | bool (true only) | Land the message with `is_trashed=true`. (Distinct from `action: "drop"` — the row stays, just hidden.)        |
| `mark_archived`  | bool (true only) | Land the message with `is_archived=true`.                                                                      |
| `skip_autoreply` | bool (true only) | Suppress the standard autoreply for this message (in addition to the `is_spam=true` suppression).              |
| `add_event`      | object[]       | Persist one `ThreadEvent` per entry, attributed to this webhook channel. See [Events](#add_event-events).        |
| `reply_draft`    | object         | `{template: "<MessageTemplate UUID>"}` — materialise a draft reply for the user to refine + send. See [Reply drafts](#reply_draft-drafts). |

Notes:

* `action: "drop"` always wins. Setting `action: "drop"` together with
  `add_labels` or `assign_to` still drops — the thread is never created,
  so neither side effect is applied.
* `is_spam` discriminates between **explicit false (ham)** and **no
  opinion**: returning `{}` leaves the dispatcher's verdict (typically
  rspamd) untouched, while returning `{"is_spam": false}` forces ham.
* `add_labels` only makes sense for **mailbox-scoped** channels: labels
  are per-mailbox. For domain- or global-scoped channels the UUIDs are
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

The `im` `content` is stored on every inbound, so it is capped at
**32 KiB** (UTF-8) — longer content is truncated. At most **20**
`add_event` entries are processed per response; extras are dropped.

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
* **add_labels**: set union across all webhooks.
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
X-StMsg-Trigger: message.delivered
X-StMsg-Instance: https://messages-public-url.example.com
X-StMsg-Channel-Id: 05f1f991-c2e9-4fa7-8a78-98c3aa904c7c
X-StMsg-Mailbox: alice@example.com
X-StMsg-Mailbox-Id: 3c2e0b1a-9d4f-4e8c-bf2a-1a2b3c4d5e6f
X-StMsg-Recipient: alice@example.com
X-StMsg-Is-Spam: false

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
message is **stored**:

* `id` and `threadId` are included **only** on `message.delivered` — it
  fires *after* the `Message` row exists, so we stamp them (the same values
  also ride in the `X-StMsg-Message-Id` / `X-StMsg-Thread-Id` headers). The
  blocking triggers (`message.inbound` / `message.delivering`) fire *before*
  the row exists — there is no id yet, so both are absent.
* `blobId`, `mailboxIds`, `keywords` are absent on **every** trigger:
  `blobId` would imply a JMAP blob-download endpoint we don't expose, and
  `mailboxIds` / `keywords` need a folder/flag mapping we haven't designed.

Attachment **bytes** are also intentionally omitted: JMAP keeps
attachment content behind a `blobId` and a separate fetch, which has no
analogue in a fire-and-forget webhook. The `attachments[]` entries
still describe each attachment's `type`, `size`, `name`, `disposition`
and `cid`. If you need the raw bytes pick `format: "eml"` instead.

#### Date formatting

`sentAt` and `receivedAt` are JMAP `UTCDate` strings: ISO-8601 in UTC
with an explicit `Z` suffix, e.g. `2026-01-01T12:00:00Z` (not
`+00:00`). This matches RFC 8621 §1.4.

### `jmap_metadata`

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
        # Body content may not be there in jmap_metadata mode.
        body_values = body.get("bodyValues") or {}
        for part_id, value in body_values.items():
            print(f"  part {part_id}: {value['value'][:80]}")
    else:
        return "unsupported", 415

    # Echo envelope metadata for logging.
    print("trigger:", request.headers["X-StMsg-Trigger"])
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
  * Redirects are followed (up to a small hop limit) but **each hop is
    re-validated and re-pinned**, so an endpoint can't 3xx-redirect the
    delivery to an internal target. The `POST` is re-issued on each hop
    (method + body preserved), so a receiver behind a load balancer or
    URL canonicaliser still gets the signed payload.
* Blocking webhooks are silent for the original sender — the inbound
  SMTP transaction has already been accepted. A blocking-drop is
  visible only through logs and the pipeline's `dropped_by_webhook`
  return value.

## Performance notes & future work

* **Per-webhook blob re-fetch (non-blocking).** Each non-blocking
  webhook for a message runs in its own task that independently
  re-fetches `Message.blob` (object-storage download + decrypt +
  decompress) and re-parses the MIME. With *K* non-blocking webhooks on
  the same message that's *K* downloads and *K* parses of identical
  bytes — wasteful for large messages. This is an accepted trade-off for
  now (each task stays self-contained and the payload never rides the
  broker). A future optimization is a **short-lived blob/parse cache**
  (keyed by `blob_id`, scoped to a single message's fan-out) so the
  bytes are fetched and parsed once and reused across that message's
  webhook tasks.

[rfc8621]: https://www.rfc-editor.org/rfc/rfc8621#section-4.1

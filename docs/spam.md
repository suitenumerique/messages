# Spam & sender-authentication processing

This document describes how Messages classifies inbound mail as spam or forged,
how that classification is configured (globally or per mail domain), how it can
be extended (native rspamd, header rules, custom webhooks, or an upstream MX
gateway), and how the results are surfaced to users.

## Overview

Every externally-received message runs through an **inbound pipeline** before it
is delivered. The pipeline is assembled per recipient mailbox and, for external
mail, runs these steps in order:

1. **Before-spam webhooks** (`message.inbound`) — user/integration webhooks that
   may drop, defer, or pre-decide the spam verdict.
2. **ARC** — holds (`RETRY`) a message whose *trusted* ARC seal couldn't be
   verified because of a DNS failure (only when `trusted_arc_sealers` is set).
   See [ARC relay-trust](#arc-relay-trust).
3. **Hardcoded rules** — deterministic `header_match` rules **and** `arc_verdict`
   relay-trust rules from config.
4. **rspamd** — native `/checkv2` scan.
5. **Inbound authentication** — DKIM/DMARC verdict (SPF indirectly).
6. **After-spam webhooks** (`message.delivering`, then `message.delivered`).

Each step returns a `Decision` (`CONTINUE` / `RETRY` / `DROP`) and may set the
spam verdict. The verdict is a tri-state `ctx.is_spam`: `None` (undecided) until
a step decides it; **the last decisive step wins**, and the hardcoded-rules and
rspamd steps are skipped entirely once a verdict already exists. This lets a
before-spam webhook, an internal-origin short-circuit, or a header rule pre-empt
rspamd.

Two distinct outcomes are produced:

- **`is_spam` (boolean)** — routes the message to the **Junk** view and
  suppresses auto-reply and push notifications. Set only by high-confidence
  signals (rspamd `quarantine`/`reject`, a matching `spam` rule, or a webhook
  override).
- **Graded UI markers** (`postmark["spam"]` = `"possible"` / `"likely"`) — the
  message still lands in the inbox but shows a "may be / likely spam" banner.

Internal mailbox-to-mailbox mail and selfcheck probes are trusted: the task
pre-sets `is_spam = False` and the pipeline omits the spam and auth steps
(webhooks still fire, so consumers can't tell the difference).

> Implementation: `core/mda/inbound_pipeline.py` (pipeline), `core/mda/spam.py`
> (rspamd + rules), `core/mda/inbound_auth.py` (DKIM/DMARC),
> `core/mda/inbound_tasks.py` (task wrapper). Webhooks are documented in
> [webhooks.md](./webhooks.md).

## rspamd native integration

Messages talks to rspamd over HTTP. The reference deployment ships an **MPA**
container (rspamd engine + nginx proxy, see `src/mpa/`) that exposes the
`/checkv2` endpoint.

On each external message, `call_rspamd()` POSTs the raw RFC-822 bytes to
`{rspamd_url}/checkv2` (`Content-Type: message/rfc822`, 10 s timeout), optionally
with an `Authorization` header, and forwards the SMTP envelope as rspamd scan
headers (`From`→`mail_from`, `Rcpt`→`rcpt_to`, `IP`→`ip`, `Helo`→`helo`,
`Hostname`→`hostname`) so rspamd can evaluate SPF, reputation, etc.

Messages **does not interpret the rspamd score**. It reads back rspamd's
`action` and maps it to a delivery outcome. This is the single source of truth
for that mapping:

| rspamd `action`            | Outcome                                                        |
|----------------------------|---------------------------------------------------------------|
| `no action`                | Deliver to inbox, not spam                                     |
| `greylist`, `soft reject`  | `RETRY` — held and re-tried (temporary defer, see below)      |
| `add header`               | Deliver to inbox, UI marker `spam = "possible"`               |
| `rewrite subject`          | Deliver to inbox, UI marker `spam = "likely"`                 |
| `quarantine`, `reject`     | `is_spam = True` → **Junk** (reject can't be honored at SMTP time, so it lands in Junk) |
| `discard`                  | `DROP` — accepted and silently blackholed, no bounce          |
| unknown / unmapped         | Deliver to inbox                                               |

If rspamd is **not configured** (`rspamd_url` absent), the step is a no-op and
the verdict is left to other steps. If rspamd is configured but **errors or is
unreachable**, the message is **held for `RETRY` (fail-closed, never
fail-open)** rather than delivered unchecked — see deferral below.

DKIM/DMARC symbols from the same `/checkv2` response are reused by the inbound
authentication step (below) when it runs in `rspamd` mode, so a message is only
scanned once.

## Configuration: global and per-domain

Spam behavior is driven by a single `SPAM_CONFIG` dictionary.

- **Global** — the `SPAM_CONFIG` Django setting (env var `SPAM_CONFIG`, a
  JSON/dict value, default `{}`).
- **Per mail domain** — a `MailDomain` may override any subset of keys via
  `MailDomain.custom_settings["SPAM_CONFIG"]`. Resolution is a shallow
  key-by-key merge over the global config:

  ```python
  spam_config = settings.SPAM_CONFIG.copy()
  if maildomain.custom_settings and "SPAM_CONFIG" in maildomain.custom_settings:
      spam_config.update(maildomain.custom_settings["SPAM_CONFIG"])
  ```

  (`MailDomain.get_spam_config()`, resolved at delivery time from the recipient
  mailbox's domain.)

There is **no per-mailbox spam configuration** — the effective scope is
global → mail domain. (A future enhancement could add a per-mailbox layer.)

### `SPAM_CONFIG` keys

| Key              | Type   | Meaning |
|------------------|--------|---------|
| `rspamd_url`     | string | Base URL of the rspamd HTTP endpoint (`/checkv2` is appended). Omit to disable rspamd. |
| `rspamd_auth`    | string | Optional value for the `Authorization` header sent to rspamd. |
| `inbound_auth`   | string | Sender-auth backend: `native`, `rspamd`, `arc`, or `authentication-results`. Omit/empty to disable DKIM/DMARC checks. |
| `trusted_relays` | int    | Number of sender-side `Received`/`Authentication-Results` blocks to trust, counting from the boundary our own MTA prepends. Default `0` (trust only our own hop). Raise this when a fixed upstream gateway sits in front. Not used by `inbound_auth: "arc"`. |
| `trusted_arc_sealers` | list | ARC sealer `d=` allowlist. **Fail closed: `[]` (or absent) trusts nothing** — you must list your sealers. Used by `inbound_auth: "arc"` and by `arc_verdict` rules, and enables the ARC `RETRY`-on-DNS-failure hold (see [ARC relay-trust](#arc-relay-trust)). |
| `rules`          | list   | Ordered rules — `header_match` / `header_match_regex` **or** `arc_verdict` trust conditions — with action `spam` / `ham` / `drop` (see below). |

### Related settings

| Setting                             | Default        | Meaning |
|-------------------------------------|----------------|---------|
| `SPAM_CONFIG`                       | `{}`           | Global spam config dict (above). |
| `MESSAGES_INBOUND_DEFERRAL_MAX_AGE` | `172800` (48h) | Max time a message may be held on `RETRY` before it is force-delivered flagged (see deferral). |

> Note: there are no dedicated `RSPAMD_*` env vars — the rspamd URL and auth
> live inside `SPAM_CONFIG` (`rspamd_url`, `rspamd_auth`).

## Sender authentication (DKIM / DMARC)

The inbound-auth step produces a **sender-auth verdict** independent of the spam
verdict, stored in `postmark["auth"]` and surfaced in the UI (below):

- `fail` → the message is a likely **forgery** (DMARC fail).
- `none` → the sender's identity **could not be verified** (DKIM not passing,
  no enforceable DMARC).
- verified → nothing recorded (no banner).

The backend is selected by `SPAM_CONFIG["inbound_auth"]`:

- **`native`** — verify the DKIM signature locally (crypto + DNS) and require
  the signing `d=` domain to align with the `From:` domain, in the mode that
  domain's DMARC record asks for: `adkim=s` wants an exact match, and the
  default `adkim=r` accepts any name sharing its organizational domain
  (per the [Public Suffix List](https://publicsuffix.org/)), so
  `mail.example.com` may sign for `From: example.com`. The `_dmarc` lookup is
  only made when it can change the answer — an exact match is aligned either
  way, and unrelated domains are unaligned either way — so it costs a query
  only for a subdomain signing for its parent, and falls back to relaxed if it
  does not complete. Never returns `fail`; worst case is `none`.

  > **This is one tag, not DMARC evaluation.** Declaring a message *failed*
  > DMARC needs SPF, since a message can pass DMARC through an aligned SPF
  > with no DKIM signature at all — so `aspf` is not honoured and a policy of
  > `p=reject` is not enforced. Use `rspamd` if you want real DMARC.
- **`rspamd`** — read DKIM/DMARC **symbols** from the rspamd `/checkv2` result
  (reusing the spam-step scan). Verdict precedence: `fail` > `pass` > `none`.
- **`arc`** — read `dkim=`/`dmarc=` from the `ARC-Authentication-Results` that a
  **trusted sealer** cryptographically sealed (RFC 8617). Plaintext headers are
  never read; an unsealed or untrusted-sealed message is `none`. See
  [ARC relay-trust](#arc-relay-trust).
- **`authentication-results`** — parse `dkim=`/`dmarc=` from the
  `Authentication-Results` header(s) added by trusted upstream relays (bounded
  by `trusted_relays`). Use this when an upstream MX gateway already does
  authentication.

### rspamd symbol → outcome (mode `rspamd`)

| Check | Symbols → `pass`        | Symbols → `fail`                                                             | Symbols → `none`          |
|-------|-------------------------|------------------------------------------------------------------------------|---------------------------|
| DKIM  | `R_DKIM_ALLOW`          | `R_DKIM_REJECT`, `R_DKIM_PERMFAIL`, `R_DKIM_TEMPFAIL`, `DKIM_INVALID`         | `R_DKIM_NA`, `DKIM_NA`    |
| DMARC | `DMARC_POLICY_ALLOW`    | `DMARC_POLICY_REJECT`, `DMARC_POLICY_QUARANTINE`, `DMARC_BAD_POLICY`          | `DMARC_NA`                |

Final rule: a DMARC `fail` yields **forged** (`auth = "fail"`); otherwise if DKIM
is not `pass`, **unverified** (`auth = "none"`); otherwise verified.

> **SPF is not surfaced as a standalone verdict.** It only influences
> classification indirectly through rspamd scoring (the envelope is forwarded to
> rspamd). The user-facing auth verdict is DKIM + DMARC only.

## ARC relay-trust

[ARC](https://datatracker.ietf.org/doc/html/rfc8617) (Authenticated Received
Chain) lets an intermediary that observed a message's original authentication
**cryptographically seal** that observation, so a downstream receiver can trust
it even after forwarding breaks SPF/DKIM. Messages uses ARC two ways, both keyed
off one allowlist, `trusted_arc_sealers`:

- **`trusted_arc_sealers`** — the `d=` domains whose seals we trust (subdomains
  match). **Fail closed:** `[]` (or absent) trusts *nothing* — anyone can
  produce a valid ARC seal, so you must list the sealers you trust. Trust is
  granted only when the chain is `cv=pass` **and** the outermost sealer is on
  the allowlist.

The result is a **binary verdict** (`core/mda/arc.py`):

| `arc_verdict` | Meaning |
|---|---|
| `trusted` | `cv=pass` **and** sealed by an allowlisted sealer |
| `untrusted` | everything else — no ARC chain, a chain from an unlisted sealer, or a chain that fails to validate |

> **We only verify seals we could trust.** If the allowlist is empty, or the
> message's outermost sealer is not on it, the chain is `untrusted` regardless of
> validity, so we skip crypto + DNS entirely. Attacker-controlled mail (which
> never names a trusted sealer) therefore triggers **zero** DNS traffic, and a
> forged chain claiming a trusted sealer is capped at 20 instances before we
> refuse to verify.

**1. As a sender-auth verdict** (`inbound_auth: "arc"`) — `dkim`/`dmarc` are read
from the trusted sealer's sealed `ARC-Authentication-Results`; untrusted/unsealed
→ `none`. See [Sender authentication](#sender-authentication-dkim--dmarc).

**2. As a gating rule** — an `arc_verdict` rule acts on the verdict:

```jsonc
{ "arc_verdict": "untrusted", "action": "drop" }   // discard (no Message)
{ "arc_verdict": "untrusted", "action": "spam" }   // route to Junk
{ "arc_verdict": "trusted",   "action": "ham"  }   // allowlist trusted mail
```

Rules are evaluated in list order (first match wins), so an `arc_verdict` rule
composes with the header rules in the same `rules` list. A `dnsfail` (below) is
indeterminate and matches **neither** verdict.

### DNS failures hold, they don't fail open

`dnsfail` is an **internal, transient** signal — never an `arc_verdict` value. A
key-record lookup that doesn't complete (timeout / SERVFAIL / NXDOMAIN / empty)
is **indeterminate**, not a forgery — NXDOMAIN in particular can be transient
(negative caching after a fresh publish, a zone mid-reload). Because we only
verify seals from a listed sealer, a `dnsfail` only ever arises for a message
**claiming one of your trusted sealers**. Such a message is held for retry
(`Decision.RETRY`) by the `arc` pipeline step — never delivered unverified,
never dropped. The hold is bounded by `MESSAGES_INBOUND_DEFERRAL_MAX_AGE` (48h
default). **Past the window** the seal is deemed unresolvable (our own relay's
DNS works, so a key that never resolved for 48h is treated as bogus) and
reclassified to a definite `untrusted` verdict — so the `arc_verdict` rules then
apply (an `untrusted` → `drop`/`spam` rule fires) rather than force-delivering it.

> **Widget submissions are exempt.** Messages from a widget channel's web form
> carry no seal by construction, so the arc step and `arc_verdict` rules skip
> them — an `untrusted` → `drop` rule never discards first-party form traffic.
> (rspamd and `header_match` rules still apply.) If no widget channel exists,
> no widget-origin mail exists in the first place.

> **Fail closed:** an empty `trusted_arc_sealers` trusts nothing — with
> `inbound_auth: "arc"` every message is then `none` (unverified), and an
> `arc_verdict: "untrusted"` rule matches every non-widget message (widget
> submissions stay exempt, per above). **Populate the allowlist**
> (it may list several sealers, e.g. an external relay plus an internal gateway)
> to actually trust anything.

> **Single-relay assumption:** the sealed `ARC-Authentication-Results` is read
> from the **outermost** ARC instance — correct for one trusted relay in front of
> you. With two or more sealing hops the outermost AAR reflects the *last* hop's
> re-evaluation (which may show `dkim=fail` for legitimately forwarded mail),
> collapsing to `none` rather than recovering the origin verdict from an inner
> instance. This fails safe (never a false "verified") and is a deliberate
> limitation, not a bug.

### Examples

Public MX, accept only trusted-ARC-sealed mail, junk the rest:

```jsonc
{
  "inbound_auth": "arc",
  "trusted_arc_sealers": ["relay.example"],
  "rules": [{ "arc_verdict": "untrusted", "action": "spam" }],
  "rspamd_url": "http://rspamd:11334/checkv2"
}
```

Third-party MX relay, ARC mandatory + honor the relay's `X-Spam` verdict. The
relay is your published MX: it scans mail, ARC-seals it (`d=relay.thirdparty.example`),
stamps `X-Spam-*` headers, then forwards to your MTA.

```jsonc
{
  // Sender-auth banner comes from the relay's sealed results.
  "inbound_auth": "arc",
  "trusted_arc_sealers": ["relay.thirdparty.example"],

  // The relay adds one hop in front of our MTA, so its X-Spam / Received
  // headers land in block 1. Trust block 0+1 (raise if it chains more hops).
  "trusted_relays": 1,

  "rules": [
    // 1. ARC mandatory: discard anything the relay didn't seal. First, so
    //    unsealed mail dies before any of its headers are trusted.
    { "arc_verdict": "untrusted", "action": "drop" },
    // 2. Honor the relay's verdict — safe because rule 1 guarantees every
    //    surviving message came through the relay.
    { "header_match": "X-Spam-Flag: YES", "action": "spam" }
  ]
  // No rspamd_url: the relay already scans.
}
```

Because `trusted_arc_sealers` is non-empty, a DNS failure verifying the relay's
seal **holds** the message (RETRY) instead of dropping it — a relay-DNS outage
never silently discards legitimate mail. Prefer `"action": "spam"` over `"drop"`
in rule 1 while validating the setup, then tighten to `drop`.

## Hardcoded rules

`SPAM_CONFIG["rules"]` is an ordered list of deterministic rules, evaluated
before rspamd. The first matching rule decides the verdict. Each rule has exactly
one condition plus an `action`:

| Field                | Meaning |
|----------------------|---------|
| `header_match`       | Literal `Header-Name: value` (case-insensitive). Must contain a colon. |
| `header_match_regex` | Regex alternative, full-match, case-insensitive. |
| `arc_verdict`        | ARC relay-trust condition — `trusted` / `untrusted` (see [ARC relay-trust](#arc-relay-trust)). |
| `action`             | `spam` / `reject` → mark spam; `ham` / `no action` → mark not-spam; `drop` → discard the message (no `Message` row). Default `spam`. |

Header rules honor `trusted_relays`: only headers within the trusted window (the
most recent `trusted_relays + 1` header blocks, newest first) are considered, so
a spammer can't forge a header that an upstream you trust would have stripped or
overwritten. The `Return-Path` header is always ignored (spoofable envelope
value). `arc_verdict` conditions ignore `trusted_relays` — they use the
cryptographic chain, not header position.

This is the primary mechanism for **honoring the verdict of an upstream filter**
(next section).

## Upstream / edge MX filtering ("en amont")

Many deployments put a dedicated anti-spam gateway **in front of** Messages at
the MX edge — it scans mail before it ever reaches a mailbox and typically
stamps its verdict into a header (e.g. `X-Spam-Flag: YES`) and/or adds its own
`Authentication-Results`. Messages accommodates this without any native scanning
of its own:

1. **Trust the gateway's position.** Set `trusted_relays` to the number of hops
   the gateway adds, so its headers fall inside the trusted window and forged
   copies from further upstream are ignored.
2. **Honor its spam verdict** with a hardcoded rule, e.g.:

   ```json
   {
     "SPAM_CONFIG": {
       "trusted_relays": 1,
       "rules": [
         { "header_match_regex": "X-Spam-Flag:\\s*YES", "action": "spam" }
       ]
     }
   }
   ```

   (Set globally or per mail domain, e.g. only for domains whose MX points at the
   gateway.)
3. **Honor its authentication results** by setting
   `inbound_auth: "authentication-results"` so the gateway's DKIM/DMARC checks
   drive the sender-auth verdict instead of re-checking locally.

You can run an upstream gateway **and** rspamd together (defense in depth):
header rules run first and short-circuit rspamd when they match, so the gateway's
decision takes precedence and rspamd only scores what the gateway passed through.

## Custom spam processor via webhooks

Webhooks can fully **replace or override** the built-in spam decision — see
[webhooks.md](./webhooks.md) for the general webhook contract; this section
covers the spam-specific behavior.

A **blocking** webhook (`message.inbound` or `message.delivering`) may return a
`2xx` JSON body containing:

```json
{ "is_spam": true }
```

which sets `ctx.is_spam` to that boolean. Only a real JSON boolean is honored;
any other value means "no opinion".

Phase determines the semantics:

- **`message.inbound` (before-spam)** runs *before* the header-rules and rspamd
  steps. Because those steps skip once a verdict exists, a before-spam webhook
  that sets `is_spam` **pre-empts rspamd entirely** — i.e. it becomes your
  spam processor.
- **`message.delivering` (after-spam)** runs *after* rspamd, so it **overrides**
  the verdict rspamd produced.
- **`message.delivered` (after-spam, fire-and-forget)** is non-blocking; its
  response is ignored and it cannot influence the verdict. It receives the final
  `is_spam` value for logging/sync.

The webhook payload carries the pending verdict in the `X-StMsg-Is-Spam` header
(`pending` while undecided during the before-spam phase, else `true`/`false`).
The response body may also drive other actions (`action: "drop"`, labels,
assignment, `skip_autoreply`, etc.) — see [webhooks.md](./webhooks.md).

Webhook channels are scoped **global / maildomain / mailbox** and must have
`Channel.is_active = True` to fire. Blocking-webhook results (including the
`is_spam` override) are cached for the deferral window and replayed on retry, so
a rspamd outage that forces a `RETRY` doesn't re-invoke an already-successful
before-spam webhook.

## What happens to a spam message

Once `is_spam = True`:

- **Foldering** — spam is a boolean flag on the message/thread, not a separate
  folder. The thread list excludes spam by default; the **Junk** view is the
  same list with `is_spam=1`. The thread's `is_spam` follows its first message,
  and spam messages are excluded from "active" counters/timestamps. `is_spam` is
  indexed for search and filtering. It is independent of `is_trashed`.
- **Auto-reply suppressed** — `should_send_autoreply()` returns early for spam
  (never vacation-reply to spam).
- **Push notifications suppressed** — no push is enqueued for a spam message.

### Deferral & "processing failed"

`RETRY` outcomes (rspamd `greylist`/`soft reject`, rspamd/webhook errors, or a
non-2xx blocking webhook) hold the message and re-try it on the inbound queue
(roughly every 5 minutes). If a message is still failing after
`MESSAGES_INBOUND_DEFERRAL_MAX_AGE` (default 48 h), it is **force-delivered**
with `is_spam = False`, auto-reply skipped, and stamped
`postmark["processing"] = "fail"` — which surfaces the "delivered without our
usual safety checks" banner (below). This guarantees mail is never lost to a
persistently failing rspamd/webhook dependency, at the cost of one un-checked
delivery.

## User-facing warnings in the reading UI

The reading view shows up to four banners, derived from the message's
`stmsg_headers` (a serializer projection of the internal `Message.postmark`
JSONField; legacy `X-StMsg-*` MIME headers are also merged for old messages).

> Naming note: "postmark" here is an **internal per-delivery pipeline record**
> (`Message.postmark`), not the Postmark email SaaS.

| Banner | Severity | `stmsg_headers` trigger | Text |
|--------|----------|-------------------------|------|
| **Forged sender** | error | `sender-auth == "fail"` | "This message failed sender authentication and is likely a forgery. Do not trust it." |
| **Unverified sender** | warning | `sender-auth == "none"` | "This contact's identity could not be verified. Proceed with caution." |
| **Processing failed** | error | `processing-failed` truthy | "This message was delivered without our usual safety checks. Please review it with caution." |
| **Suspected spam** | warning | `spam == "likely"` / `"possible"` | "This message is likely spam…" / "This message may be spam. Review it with caution." |

The sender chip also shows an icon + tooltip: a `warning` icon for unverified
and a `gpp_bad` icon for forged senders.

### How each warning maps to the pipeline

| Banner            | Source signal | Produced by |
|-------------------|---------------|-------------|
| Forged / Unverified sender | `postmark["auth"]` = `fail` / `none` | Inbound-auth step (DKIM/DMARC) — for mode `rspamd`, from the symbol table above |
| Suspected spam    | `postmark["spam"]` = `possible` / `likely` | rspamd **action** `add header` → possible, `rewrite subject` → likely |
| Processing failed | `postmark["processing"]` = `fail` | Deferral-window expiry force-delivery (any persistently failing step) |

Note the two-tier design: rspamd's mid-confidence actions (`add header`,
`rewrite subject`) produce an **inbox banner**, while its high-confidence actions
(`quarantine`, `reject`) set `is_spam` and route to **Junk** with no banner.

## User "report as spam" action

Users with edit rights can flag a message/thread as spam (or un-flag it) via the
flag API (`spam` flag). This sets the `is_spam` boolean (cascading to draft
children) and recomputes thread stats, moving the thread in/out of the Junk view.

> **Gap:** this action is purely local foldering — it does **not** send any
> feedback to rspamd (no `learnspam`/`learnham`/`fuzzy` training call exists in
> the backend). Wiring the report-as-spam / not-spam actions into rspamd's learn
> endpoints is tracked as a future enhancement (issue #509).

## Not yet implemented / future work

- **rspamd learn feedback** from the report-as-spam button (#509).
- **Per-mailbox** spam configuration (currently global → maildomain only).
- **SPF** as a standalone user-visible verdict (today it only feeds rspamd
  scoring; the surfaced auth verdict is DKIM + DMARC).
- **Native DMARC evaluation** — mode `native` reads `adkim` but cannot enforce
  a policy or honour `aspf` without an SPF evaluator, so it never returns
  `fail`. DMARC aggregate/failure reporting (RFC 9990/9991) is not implemented
  in any mode.

## Implementation map

| Area | File |
|------|------|
| Pipeline assembly, rspamd action mapping, deferral constants | `core/mda/inbound_pipeline.py` |
| rspamd `/checkv2` client, hardcoded header rules | `core/mda/spam.py` |
| DKIM/DMARC verdict, rspamd symbol table, AR parsing | `core/mda/inbound_auth.py` |
| DMARC record discovery (`adkim`, org-domain fallback) | `core/mda/dmarc.py` |
| Task wrapper, internal/selfcheck short-circuit, force-delivery | `core/mda/inbound_tasks.py` |
| Webhook dispatch, phases, `is_spam` override, result cache | `core/mda/dispatch_webhooks.py` |
| `SPAM_CONFIG` resolution (`get_spam_config`) | `core/models.py` (`MailDomain`) |
| `is_spam` flag, `postmark` field, `stmsg_headers` projection | `core/models.py` (`Message`/`Thread`) |
| Spam / Junk view filtering | `core/api/viewsets/thread.py` |
| Report-as-spam flag action | `core/api/viewsets/flag.py` |
| UI warning banners | `src/frontend/.../thread-message/thread-message-header.tsx` |
| rspamd engine + proxy container | `src/mpa/` |

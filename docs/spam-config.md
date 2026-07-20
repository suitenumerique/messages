# `SPAM_CONFIG`

Inbound spam filtering **and** sender-authentication config. Set globally via the
`SPAM_CONFIG` env var (JSON), overridable per mail domain through
`MailDomain.custom_settings["SPAM_CONFIG"]` (merged by
`MailDomain.get_spam_config()`).

> Naming note: these keys cover both spam scoring and sender auth/trust. The
> auth keys live here because `inbound_auth` already did — not because they are
> "spam". A future split into a dedicated auth/trust config is possible but out
> of scope here.

## Keys

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `rspamd_url` | string | — | rspamd `/checkv2` endpoint. Absent = no rspamd scan. |
| `rspamd_auth` | string | — | Optional `Authorization` header value for rspamd. |
| `rules` | list | `[]` | Hardcoded header-match spam rules (see below). |
| `trusted_relays` | int | `0` | How many upstream `Received` blocks to trust for the header-match `rules`. Block 0 = the block our own MTA prepends. Not used by `inbound_auth: "arc"`. |
| `inbound_auth` | string\|null | `null` | Sender-auth backend for the verdict/banner (see below). |
| `trusted_arc_sealers` | list | `[]` | ARC sealer `d=` allowlist. `[]` = accept any valid seal. Used by `inbound_auth: "arc"` and `arc_gate`. |
| `arc_gate` | string | `"off"` | Action when a message is **not** sealed by a trusted sealer (see below). |

## `rules`

Each rule matches one header, within the `trusted_relays` block window:

```jsonc
{ "header_match":       "X-Foo: exact-value", "action": "spam" }  // literal, case-insensitive
{ "header_match_regex": "X-Spam-Level:\\*{5,}", "action": "spam" } // regex, IGNORECASE
```

`action`: `"spam"` / `"reject"` → `is_spam=True`; `"ham"` / `"no action"` →
`is_spam=False`. Default `"spam"`. First matching rule wins. `Return-Path` is
never eligible (spoofable envelope value).

## `inbound_auth` — the verdict / banner

Produces `postmark["auth"]`: absent = verified, `"none"` = unverified,
`"fail"` = likely forged (DMARC disavowal).

| Value | Source |
| --- | --- |
| `"native"` | Local DKIM verify + strict `From`/`d=` alignment. |
| `"rspamd"` | dkim/dmarc from the rspamd result. |
| `"arc"` | dkim/dmarc from a trusted sealer's **sealed** `ARC-Authentication-Results` only. Plaintext headers are never read. Untrusted/unsealed → unverified. |
| `"authentication-results"` | Parse `dkim=`/`dmarc=` from the top-level `Authentication-Results`, trusted by `trusted_relays` position. |
| `null` / absent | Disabled. |

## `arc_gate` — relay-trust enforcement

Verifies the ARC chain (dkimpy) and applies an action when the message is **not**
sealed by a trusted sealer (`cv=pass` **and** outermost sealer ∈
`trusted_arc_sealers`, or any `cv=pass` when the allowlist is empty).

| Value | Effect |
| --- | --- |
| `"off"` | No gating (default). |
| `"spam"` | Not trusted-sealed → `is_spam=True` (Junk). |
| `"drop"` | Not trusted-sealed → silently discarded. |

Runs first among the spam steps, so an untrusted verdict is authoritative. A DNS
/ verification failure never spams or drops (can't verify ≠ forged). `quarantine`
and `reject` are planned for a follow-up.

> **Public-MX warning:** with an empty `trusted_arc_sealers`, "any valid seal"
> is bypassable — an attacker self-seals their own domain (`cv=pass`,
> `d=attacker.example`). On a publicly reachable MX, **populate the allowlist**
> so the ARC seal is a real trust anchor. `trusted_arc_sealers` may list several
> sealers (e.g. an external relay plus an internal gateway).

## Examples

Public MX, accept only trusted-ARC-sealed mail (mark the rest as spam):

```jsonc
{
  "inbound_auth": "arc",
  "trusted_arc_sealers": ["relay.example"],
  "arc_gate": "spam",
  "rspamd_url": "http://rspamd:11334/checkv2",
  "trusted_relays": 99,
  "rules": [ { "header_match_regex": "X-Spam-Level:\\*{5,}", "action": "spam" } ]
}
```

Minimal ARC gate, no rspamd:

```jsonc
{ "inbound_auth": "arc", "trusted_arc_sealers": ["relay.example"], "arc_gate": "spam" }
```

Multiple sealers (external relay + internal gateway):

```jsonc
{ "inbound_auth": "arc", "trusted_arc_sealers": ["relay.example", "gateway.internal.example"], "arc_gate": "spam" }
```

Legacy header-based auth (no ARC):

```jsonc
{ "inbound_auth": "authentication-results", "trusted_relays": 1 }
```

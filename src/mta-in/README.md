# ST Messages MTA inbound

The MTA is in charge of receiving emails from the Internet and pushing them to the MDA and ultimately the users.

It only deals with inbound email and won't even send bounces by itself.

The MTA is entirely stateless and configured from env vars.

## Two implementations, same contract

This directory ships **two** implementations in parallel. Both expose the same SMTP behaviour to the outside world and speak the same MDA REST contract. pymta is the one to deploy; Postfix is kept as the migration fallback (see [Which one to use](#which-one-to-use)):

| | Postfix + milter (**legacy**) | pymta (**recommended**) |
|---|---|---|
| Compose service | `mta-in` (host port `8910`) | `mta-in-py` (host port `8920`) |
| Image | `Dockerfile` | `Dockerfile.pymta` |
| SMTP server | Postfix `smtpd` | `aiosmtpd 1.4.6` |
| MDA glue | `src/delivery_milter.py` + `src/api/mda.py` (sync `requests`) | `src/pymta/*` (async `httpx`) |
| Prometheus metrics | none | `/metrics` on port `9100` |
| Tests | `make test-mta-in` | `make test-mta-in-py` |
| Lint | `make lint-mta-in` | `make lint-mta-in-py` |

Both run as a stateless, queue-less SMTP front-end. After receiving an email through SMTP each message is processed synchronously during the SMTP session by:

- Validating each recipient with a REST API call to `{env.MDA_API_BASE_URL}/inbound/mta/check/` during the RCPT TO command.
- Delivering the complete message via REST API call to `{env.MDA_API_BASE_URL}/inbound/mta/deliver/` during the DATA command.
- Translating the MDA outcome into a single SMTP reply line.

### Translating the MDA outcome

Losing a legitimate message is worse than asking the sender to retry, so the permanent-rejection set is an explicit allow-list and everything else defers.

The table below maps the response to `deliver/`; `check/` has its own table under it.

| MDA `deliver/` response | SMTP reply | Why |
|---|---|---|
| `200` + `{"status": "ok"}` | `250` | Delivered to every recipient. |
| `400`, `413`, `415` | `554` / `5xx` | The message itself is unacceptable: unparseable, oversize, or wrong content type. A retry sends the same bytes. |
| `207 Multi-Status` | `451` | Partial delivery. The MDA cannot ask us to retry only the failed recipients, so the whole envelope must be retried. That duplicates for the recipients already served, which is preferable to losing the rest. Requires per-recipient delivery to be idempotent on the MDA side. |
| `401`, `403`, `429`, `404`, any other 4xx | `451` | Secret rotation, `exp` clock skew, throttling, a bad route. Operational events, not verdicts on the mail. |
| `5xx`, timeout, transport error | `451` | Upstream is unhealthy; also counts toward the circuit breaker. |
| `200` with an unrecognised body | `451` | Not proof of delivery. |

Only 5xx and transport failures feed the circuit breaker. A `207` or a `401` is a complete answer from a healthy MDA.

`check/` names **no** permanent statuses at all. Every entry above is a verdict on a *message*, and a recipient check carries none: a `400`, `413` or `415` there is a fault in the check request we built, so saying `554` would tell the sender a working address is permanently bad. The only permanent reply at RCPT TO comes from a `200` whose body says the mailbox does not exist.

| MDA `check/` response | SMTP reply | Why |
|---|---|---|
| `200` + `{"<addr>": true}` | `250` | Mailbox exists. |
| `200` + `{"<addr>": false}` | `550` (`421` at the miss cutoff) | The MDA says there is no such mailbox. The only permanent rejection on this path. |
| `200` naming the address with anything but a bool | `451` | Not an answer about this mailbox. |
| `200` not naming the address at all | `451` | Empty body, unparseable body, a proxy's own 200 page, or a drifted response shape. |
| any non-200, timeout, transport error, open breaker | `451` | Nothing here is a verdict on the mailbox. |

The last two rows are the reason `MDAResult.payload` is normalised to a dict and read strictly: the MDA returns one boolean per address it was asked about, so a missing key never legitimately means "no such mailbox" — it means we did not get the answer. Treating it as a miss would bounce real mail whenever something upstream of the MDA substituted the response.

### MDA wire contract

Each MTA → MDA call is an HTTP `POST` carrying:

- **Body**: for `check/`, an `application/json` document `{"addresses": [...]}`; for `deliver/`, the full RFC 5322 message as `message/rfc822`.
- **Authorization**: `Bearer <jwt>` where the JWT is signed HS256 with `env.MDA_API_SECRET` and carries:
  - `exp`: `env.MDA_API_JWT_TTL` seconds from issuance, anchored in UTC. It has to cover the whole request *plus* clock skew against the MDA.
  - `body_hash`: `sha256(body).hexdigest()`, which binds the token to the exact bytes posted, so a captured token cannot be reused to send *different* content. The MDA tracks no nonce, so replaying the same token with the same body until `exp` is not prevented; the two claims bound a captured token together, one in content and one in time.
  - Plus, for `deliver/`, envelope metadata claims (`sender`, `original_recipients`, `client_address`, `client_port`, `client_hostname`, `client_helo`, `size`).
- **Response**: see the outcome table above.

## Which one to use

**Use pymta.** It is the recommended implementation and the one being developed. Postfix + milter is legacy: it still runs, its tests still pass, and it remains the fallback if something goes wrong during migration, but new work goes to pymta and the Postfix path will be retired.

The case for pymta is smaller attack surface and better operability: no Postfix binary, no `libmilter` C glue, no on-disk queue, a distroless image that runs with a read-only root filesystem and no writable mount at all, Prometheus metrics, and per-IP admission control the Postfix config disables. It is also simply easier to change, which is why the security fixes in this directory landed there first.

Two things pymta does that Postfix does not, worth knowing before switching: it enforces a per-IP concurrent session cap (the shipped Postfix config turns per-client limits off entirely), and it holds each message in memory rather than spooling to disk, so concurrency times message size is a real memory ceiling. Both are covered below.

Switching production from one to the other only requires re-pointing the inbound public IP to the other container: the MDA back-end is unchanged, and pymta reads the Postfix image's variable names as fallbacks so an existing env file keeps working (see [Naming](#naming)).

## Running

```bash
# Default Postfix-based service
make test-mta-in
make lint-mta-in

# Pure-Python (aiosmtpd) service
make test-mta-in-py
make lint-mta-in-py
```

The shared test suite under `tests/` runs against both via the `MTA_HOST` / `MTA_PORT` env vars. A few tests assert implementation-specific behaviour and skip on the other impl (e.g. `tests/test_metrics.py` is pymta-only; the strict NUL-byte rejection test skips on Postfix). The fixtures `mta_impl`, `mta_address`, and `mta_metrics_url` in `tests/conftest.py` are how a test sees which impl it is running against.

## Environment variables

Both images are stateless and configured entirely from the environment. Each section below is the **complete** list its image reads; nothing else in the environment reaches either process. `deploy/env/mta-in.defaults` is loaded by both compose services, with `deploy/env/mta-in-py.defaults` layered on top for pymta.

### Postfix + milter (`mta-in`)

Most of this image's behaviour lives in `etc/main.cf` rather than in the environment; `entrypoint.sh` translates only the variables below into `main.cf` directives before starting Postfix.

| Variable | Default | Purpose |
|---|---|---|
| `MDA_API_BASE_URL` | none — **required** | Base URL of the MDA REST API, with trailing slash. Unset, every MDA call raises and the milter tempfails, so all mail defers |
| `MDA_API_SECRET` | none — **required** | HS256 shared secret for the MDA bearer token. Unset, it fails the same way. This image has no startup validation of either; pymta warns instead |
| `MDA_API_TIMEOUT` | `30` | Per-attempt HTTP timeout (s) for MDA calls |
| `MDA_API_JWT_TTL` | `MDA_API_TIMEOUT * 10 + 60` (`360`) | Lifetime (s) of the JWT signed for each MDA call. Much longer than pymta's because this client retries internally (urllib3 `total=5`, `backoff_factor=1`) on one token, so it has to outlive every attempt plus the backoff sleeps |
| `MAX_INCOMING_EMAIL_SIZE` | `10240000` | Total message size cap → `message_size_limit` |
| `MYHOSTNAME` | Postfix's own default | → `myhostname`; the banner and `Received` host name |
| `MYORIGIN` | Postfix's own default | → `myorigin` |
| `MYDOMAIN` | Postfix's own default | → `mydomain` |
| `ENABLE_PROXY_PROTOCOL` | `false` | → `postscreen_upstream_proxy_protocol = haproxy`. `haproxy` enables it; unset / `false` / `0` / `off` / `no` disable it; **anything else exits non-zero at startup** rather than being read as off, since a value like `true` is someone asking for PROXY protocol and booting without it would stamp every message with the balancer's IP |
| `STARTTLS_CHAIN_FILES` | empty | Comma-separated PEM bundle(s) → `smtpd_tls_chain_files`. Non-empty also switches `smtpd_tls_security_level` to `may` and loads the post-quantum `openssl.cnf` |
| `EXEC_CMD` | `false` | Start Postfix + milter, then run the container command (used by `make test-mta-in`) |
| `EXEC_CMD_ONLY` | `false` | Skip startup entirely and exec the container command (used by ad-hoc tooling) |

The limits pymta exposes as `PYMTA_MAX_RECIPIENTS_PER_ENVELOPE` / `PYMTA_MAX_ERRORS_PER_SESSION` are fixed in `etc/main.cf` here (`smtpd_recipient_limit = 100`, `smtpd_hard_error_limit = 50`), and per-client rate limiting is disabled outright (`smtpd_client_event_limit_exceptions = static:all`). Changing them means editing that file, not the environment.

### pymta (`mta-in-py`)

Grouped as `src/pymta/settings.py` groups them.

**MDA back-end**

| Variable | Default | Purpose |
|---|---|---|
| `MDA_API_BASE_URL` | `http://localhost:8000/api/v1.0/` | Base URL of the MDA REST API. A missing trailing slash is added. Warns at startup on `http://` to a non-localhost host |
| `MDA_API_SECRET` | empty | HS256 shared secret for the MDA bearer token. Warns at startup when empty or under 32 bytes |
| `MDA_API_TIMEOUT` | `30` | HTTP timeout (s) for MDA calls |
| `MDA_API_JWT_TTL` | `MDA_API_TIMEOUT + 90` | Lifetime (s) of the JWT signed for each MDA call. Must cover the request plus clock skew against the MDA |
| `MDA_BREAKER_THRESHOLD` | `10` (0 = off) | Consecutive MDA failures before short-circuiting to 451. pymta-only; the milter has no breaker and ignores it |
| `MDA_BREAKER_COOLDOWN` | `30` | Seconds the breaker stays open before probing the MDA again. Must be ≥ 1: a zero would reopen the breaker to the very next call, which neuters it rather than disabling it — use the threshold for that |

**SMTP listener and identity**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_SMTP_BIND_HOST` / `PYMTA_SMTP_BIND_PORT` | `0.0.0.0` / `25` | Where the SMTP listener binds |
| `PYMTA_SMTP_HOSTNAME` | `mta-in` | What it calls itself: banner and `Received`-header host name |
| `PYMTA_SMTP_IDENT` | `ESMTP` | Banner text after the hostname; kept version-less on purpose |
| `PYMTA_ENABLE_SMTPUTF8` | `true` | Advertise SMTPUTF8 in EHLO |

**Message and session limits**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_MAX_INCOMING_EMAIL_SIZE` | `10485760` (10 MiB) | Total message size cap, advertised as the ESMTP `SIZE` value. The final fallback matches the MDA's own `MAX_INCOMING_EMAIL_SIZE` default, so neither side accepts what the other refuses |
| `PYMTA_MAX_RECIPIENTS_PER_ENVELOPE` | `100` | RCPT TO cap per envelope |
| `PYMTA_MAX_ENVELOPES_PER_SESSION` | `10` | MAIL FROM…DATA cycles per TCP session |
| `PYMTA_MAX_ERRORS_PER_SESSION` | `50` | 4xx/5xx replies before forcing 421 + disconnect (Postfix's `smtpd_hard_error_limit`) |
| `PYMTA_MAX_RCPT_MISSES_PER_SESSION` | `10` | Unknown-mailbox lookups before 421 + disconnect |
| `PYMTA_MAX_CONCURRENT_DATA` | `40` | Messages held in memory at once. **The bound on memory**, see below. `0` disables far more than the memory bound — see the warning under "The session cap is derived" |
| `PYMTA_MAX_LINE_LENGTH` | `65536` | Longest single line accepted, in octets. aiosmtpd's RFC-strict 1001 permanently rejects mail Postfix accepts |

**Timeouts**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_COMMAND_TIMEOUT` | `120` | Idle timeout (s) between complete commands; re-armed by each accepted command |
| `PYMTA_DATA_TIMEOUT` | `300` | Hard DATA-phase deadline (s): 354 → last body byte → MDA deliver → reply. Nothing in a DATA phase outlives it |
| `PYMTA_SESSION_TIMEOUT` | `1800` (0 = off) | Wall-clock ceiling (s) on one TCP session, armed at connect and never re-armed |
| `PYMTA_SHUTDOWN_TIMEOUT` | `25` | Drain deadline on SIGTERM before abandoning in-flight sessions (s) |
| `PYMTA_PROXY_PROTOCOL_TIMEOUT` | `5` | Seconds to wait for the PROXY header before dropping the connection |

**PROXY protocol and STARTTLS**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_ENABLE_PROXY_PROTOCOL` | `false` | Enable PROXY-protocol v1/v2. `haproxy` is an alias for `true` — it is the only protocol postscreen defines and the only one implemented here — so both names take either spelling |
| `PYMTA_BLOCKED_NETWORKS` | empty | Comma-separated IPs/CIDRs refused at connect time with `554 5.7.1 Access denied`, before the banner. Matched against the **client** address: the PROXY-header source when PROXY protocol is on, the TCP peer otherwise. Nothing to do with `PYMTA_TRUSTED_PROXIES`. Permanent rather than a 421 on purpose, since a deferral invites the retries you are trying to shed; a block covering a legitimate sender therefore bounces their mail, so keep the ranges narrow |
| `PYMTA_TRUSTED_PROXIES` | empty | Comma-separated IPs/CIDRs allowed to send a PROXY header. Strongly recommended when PROXY protocol is on: empty means *every* peer's header is trusted, and startup only warns. Ignored when PROXY protocol is off |
| `PYMTA_TLS_CERT_FILE` / `PYMTA_TLS_KEY_FILE` | empty | STARTTLS cert + key paths, comma-separated for dual certificates (see below). **Both or neither** — one alone is refused at startup, since it would serve plaintext and simply not advertise STARTTLS. Both empty disables STARTTLS deliberately. A single PEM holding both the key and the chain (the Postfix layout) is given to *both*: `ssl.load_cert_chain` reads each from the same file |

**Metrics, logging, container**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_METRICS_BIND_HOST` / `PYMTA_METRICS_BIND_PORT` | `0.0.0.0` / `9100` | Prometheus endpoint (set port to 0 to disable) |
| `PYMTA_METRICS_API_KEY` | empty | Bearer token required to scrape `/metrics`. Empty = unauthenticated, with a `SECURITY:` warning at startup. Same opt-in semantics as the backend's `PROMETHEUS_API_KEY` |
| `PYMTA_LOG_LEVEL` | `INFO` | Root log level: `CRITICAL`, `ERROR`, `WARNING`, `INFO` or `DEBUG` |
| `PYMTA_LOG_VERBOSE_LIBRARIES` | `false` | Let aiosmtpd/httpx/httpcore log too. Debugging only: aiosmtpd writes every envelope address and every message body |
| `EXEC_CMD` | `false` | Start pymta in the background, then run the container command (used by `make test-mta-in-py`) |
| `EXEC_CMD_ONLY` | `false` | Skip startup entirely and exec the container command (used by `make lint-mta-in-py`) |

Every value is checked at startup and a bad one stops the process rather than being quietly ignored: booleans take `1/true/yes/on` or `0/false/no/off` and nothing else, integers must parse and clear their minimum (those marked `0 = off` accept zero, the rest reject it), `PYMTA_LOG_LEVEL` must name a real level, and `PYMTA_TRUSTED_PROXIES` must parse as IPs/CIDRs. Reading a typo as its opposite is how a security toggle ends up off in production, so nothing here has a silent fallback.

The local-part and domain length caps are **not** configurable. They are the RFC 5321 §4.5.3.1 constants (64 / 255 octets), and raising them would only widen the gap between what pymta accepts at RCPT and what the MDA can store.

Two more limits come from aiosmtpd itself and are inherited at their defaults: a 512-octet command line (§4.5.3.1.4) and 5 unrecognised commands per session before it hangs up. A third, aiosmtpd's 1001-octet line length (§4.5.3.1.6), is the one pymta overrides — `PYMTA_MAX_LINE_LENGTH` replaces it. That value is the `StreamReader` limit, so an endless line is still refused at the transport rather than buffered, and it bounds DATA lines only: commands stay under the separate 512-octet cap however high it is set. Two aiosmtpd options are deliberately left unset: `require_starttls`, because refusing a sender that does not offer STARTTLS loses mail rather than protecting it on a public MX, and the authenticator hooks, because AUTH is answered `502` outright.

### Crisis toggles

Six settings exist to be changed while something is going wrong. All six are re-read on **SIGHUP**, so they take effect on the next connection without a restart and without cutting in-flight sessions.

| Variable | Effect |
|---|---|
| `PYMTA_DRAIN` | `421 4.3.2` in place of the banner, without stopping. SIGTERM does this on its own, so reach for the var only to quiesce a node you are keeping up |
| `PYMTA_DEFER_ALL` | `451` at every RCPT, and the MDA is not consulted at all |
| `PYMTA_BLOCKED_NETWORKS` | `554` at connect for these client IPs/CIDRs |
| `PYMTA_BLOCKED_SENDER_DOMAINS` | `554` at MAIL FROM, matched case-insensitively on the domain |
| `PYMTA_BLOCKED_RECIPIENTS` | `550` at RCPT TO, full addresses, case-insensitive |

Plus the ceilings, which are consulted per connection or per command and so can move safely under a running process: `PYMTA_MAX_CONCURRENT_DATA` (and `PYMTA_MAX_SESSIONS_TOTAL`, which follows it), `PYMTA_MAX_ERRORS_PER_SESSION`, `PYMTA_MAX_RCPT_MISSES_PER_SESSION`, `PYMTA_DATA_TIMEOUT`, `PYMTA_SESSION_TIMEOUT`, `PYMTA_SHUTDOWN_TIMEOUT`. Shedding memory pressure is therefore a SIGHUP rather than a restart. Lowering a session cap mid-incident refuses new connections until the live count falls back under it, which is usually what you want.

`kill -HUP 1` re-reads exactly these. Nothing else: the SMTP options, the listener socket, the TLS context and the session caps are captured when their object is built, so re-reading them would change this module and not the server, which is worse than not offering it. A value that fails to parse leaves the running configuration untouched and logs; a typo under pressure must not be worse than not reloading.

**Rollouts: SIGTERM is the whole protocol.** One signal, no env var, no orchestration:

1. refuse new sessions with `421`, keeping the listener open,
2. wait for the in-flight ones,
3. exit as soon as the last finishes, or at `PYMTA_SHUTDOWN_TIMEOUT`.

So `docker stop`, a Kubernetes rolling update, or `systemctl restart` already does the right thing:

```yaml
- name: Roll pymta
  ansible.builtin.command: docker stop --timeout 30 pymta
```

Set the stop timeout above `PYMTA_SHUTDOWN_TIMEOUT` (default 25 s) so the runtime does not SIGKILL a process that is still draining politely.

The listener deliberately stays **open** while draining. Closing it refuses the TCP connection, which a sender cannot tell from a network fault, so it keeps the message queued against this host. Answering `421` is what moves traffic to your other MX: RFC 5321 §3.1 permits a `421` in place of the `220` greeting, and Postfix and Exim both treat it as a *per-host* defer, trying the next MX in preference order immediately. Better than a TCP refusal, and far better than DNS changes, which wait on TTLs.

Exit is prompt: the drain polls four times a second and returns the moment the last session ends, so a quiet node restarts in milliseconds rather than sleeping out a fixed delay. A second SIGTERM is ignored on purpose, since honouring it would cut exactly the sessions the first one promised to protect.

`PYMTA_DRAIN` remains for the case SIGTERM does not cover: taking a node out of rotation while leaving the process up, to look at it. Set it, `kill -HUP 1`, and watch `pymta_sessions_active` fall to zero.

### Where the defaults come from

Each limit is set against what Postfix does for the same thing, since the two run side by side and a switchover should not change who gets served. `postconf -d` on the shipping image, versus what we ship:

| | Postfix default | Our `main.cf` | pymta default |
|---|---|---|---|
| Per-IP concurrent sessions | `smtpd_client_connection_count_limit` = 50 | **disabled** (`smtpd_client_event_limit_exceptions = static:all`) | a share of the total, no flag |
| Per-IP session rate | `smtpd_client_connection_rate_limit` = 0 (off) | disabled | none, as Postfix |
| Global concurrent sessions | `default_process_limit` = 100 processes | 100 | 120, derived from a 40-message DATA cap |
| Recipients per envelope | `smtpd_recipient_limit` = 1000 | 100 | 100 |
| Errors per session | `smtpd_hard_error_limit` = 20 | 50 | 50 |
| Command idle timeout | `smtpd_timeout` = 300 s | 300 s | 120 s |
| Line length | `line_length_limit` = 2048 (wraps output) | 2048 | 65536 (accepts) |

Two need explaining. Per-IP concurrency is a tightening, not a loosening: the shipping Postfix config turns per-client limits off entirely, so any per-source ceiling at all is stricter than production is today. pymta has no flag for it — a source's ceiling is its share of `PYMTA_MAX_SESSIONS_TOTAL`, which for a host arriving alone is half of it (60 of 120) and shrinks as others turn up. Read the queue-flush note in the checklist below before lowering the total. Global concurrency is 10x Postfix's, which is reasonable for one async process rather than 100 forked ones, but it sets the memory ceiling:

> **`PYMTA_MAX_CONCURRENT_DATA` (40) is the bound on memory.** aiosmtpd holds each message in RAM with no spool file, so messages in flight times the size cap is the heap. The session caps do not bound it: a connection costs a few kB until it says DATA.
>
> Size it against the memory you gave the container. A message peaks at about **2.2x** its size — aiosmtpd needs a second copy while it joins the received lines, and freed arenas are not returned promptly — so:
>
> ```
> peak RSS  ~=  PYMTA_MAX_CONCURRENT_DATA x PYMTA_MAX_INCOMING_EMAIL_SIZE x 2.2  +  ~64 MiB
> ```
>
> | memory limit | a reasonable `PYMTA_MAX_CONCURRENT_DATA` at 10 MiB messages |
> |---|---|
> | 256 MiB | 8 |
> | 512 MiB | 20 |
> | 1 GiB | 40 (the default) |
> | 2 GiB | 90 |
> | 4 GiB | 183 |
>
> A message over the limit is refused at the `DATA` command, **before the `354`**, so a refused peer never starts uploading and the refusal costs one reply instead of a buffer. It gets `451`: delayed, not lost.
>
> **The session cap is derived, not configured.** `PYMTA_MAX_SESSIONS_TOTAL = PYMTA_MAX_CONCURRENT_DATA x 3` (120 on the default), because a session is only *in* DATA for part of its life — the rest is the handshake and the RCPT checks — so about three sessions are needed to keep one message slot busy at typical sizes. Fewer wastes the memory set aside for messages; more only admits connections that must queue for a slot, each having paid for its RCPT checks against the MDA before finding that out. It keeps the `PYMTA_` prefix regardless: the prefix says whose setting it is, not where the value came from.
>
> ⚠️ **`PYMTA_MAX_CONCURRENT_DATA=0` removes every connection limit, not just the memory one.** The session cap is derived from it, so a zero makes `PYMTA_MAX_SESSIONS_TOTAL` zero too, and the gate reads a zero cap as "no refusal" — the global cap and the per-source share both come off, and one host can hold unlimited connections. Use `0` in dev and test only; to let more mail through in production, raise the number. The startup `memory_ceiling` line reports `bounded=false` when you are in this state.
>
> **There is no per-source flag either.** A fixed per-source cap divides the slots by a constant, which decides in advance how few hosts can take everything: 20 slots at 5 each is four hosts, and no choice of constant changes that shape. Instead the gate gives each source an equal share of what exists, keeping one share spare:
>
> ```
> share = max(1, slots / (sources currently active + 1))
> ```
>
> | sources present | each may hold (of 40) |
> |---|---|
> | 1 | 20 |
> | 3 | 10 |
> | 9 | 4 |
> | 39 | 1 |
>
> So filling the slots takes **as many hosts as there are slots** — 40, not 4 — which is the most any scheme can demand without evicting work already in progress. The spare share is what stops the hosts already here from locking out a newcomer.
>
> It also removes the throttle on a large sender that a fixed cap imposes: alone at 3am, Gmail gets half the slots rather than a fixed few, and its share shrinks only when someone else actually turns up. The same sharing applies to connection slots, so one host cannot take all 120 either.
>
> Note the two ceilings are not the same number: the share bounds what *one host* can hold, the 40 slots bound what *everyone together* can. Memory has to be sized against the 40, not against the share — 40 x 10 MiB x 2.2 is about 944 MiB, so the shipped defaults want a container of 1 GiB or more. Drop `PYMTA_MAX_CONCURRENT_DATA` to 20 for 512 MiB.
>
> **One oversized message is a floor no concurrency limit lowers.** `PYMTA_MAX_INCOMING_EMAIL_SIZE x 2.2` has to fit on its own, so a 400 MB size cap needs roughly a gigabyte whatever the concurrency is set to.

### Watching the limits

Every configurable limit publishes its value as `pymta_config_limit{name="<setting minus the PYMTA_ prefix>"}`, and every limit that can reject something increments `pymta_security_rejections_total{reason=...}` under the same name. A dashboard plots one against the other, and an alert fires on approach, without the deployment's numbers being copied into the alert rule.

| Limit | Value gauge | Fires |
|---|---|---|
| `PYMTA_MAX_INCOMING_EMAIL_SIZE` | `name="max_incoming_email_size"` | `reason="oversize_announced"` (MAIL FROM `SIZE=`) or `"oversize_message"` (the body itself) |
| `PYMTA_MAX_RECIPIENTS_PER_ENVELOPE` | ✓ | `reason="max_recipients_per_envelope"` |
| `PYMTA_MAX_ENVELOPES_PER_SESSION` | ✓ | `reason="max_envelopes_per_session"` |
| `PYMTA_MAX_ERRORS_PER_SESSION` | ✓ | same reason, plus `pymta_disconnects_421_total` |
| `PYMTA_MAX_RCPT_MISSES_PER_SESSION` | ✓ | same reason, plus `pymta_disconnects_421_total` |
| `PYMTA_MAX_SESSIONS_TOTAL` (derived) and the per-source share | ✓ | `pymta_connections_total{result="rejected_global\|rejected_per_ip"}` |
| `PYMTA_MAX_CONCURRENT_DATA` and its per-source share | ✓ | `pymta_data_phases_active`, and `reason="max_concurrent_data"` |
| `PYMTA_SESSION_TIMEOUT` | ✓ | `reason="session_timeout"` |
| `PYMTA_DATA_TIMEOUT` | ✓ | `reason="data_timeout"` |
| `PYMTA_COMMAND_TIMEOUT` | ✓ | `reason="command_timeout"`, plus `pymta_disconnects_421_total`. aiosmtpd closes the idle socket silently; `HardenedSMTP` overrides that to answer 421 first, which is both friendlier and what makes the counter possible |
| `MDA_BREAKER_THRESHOLD` / `_COOLDOWN` | — | `pymta_mda_breaker_open` is 1 while open; `pymta_mda_request_duration_seconds{result="breaker_open"}` counts the calls it shed |

`pymta_sessions_abandoned_total` counts sessions cut by the `PYMTA_SHUTDOWN_TIMEOUT` drain deadline — non-zero on every rollout means the deadline is too short for your traffic.

### Naming

Every variable pymta reads is namespaced: `PYMTA_*` for the SMTP server itself, `MDA_*` for the channel to the MDA. It reads no unprefixed variable and therefore shares none with the Postfix image, so neither can change the other's behaviour by accident. `MYORIGIN` and `MYDOMAIN` have no counterpart at all, because pymta never rewrites or completes an envelope address: it requires a fully-qualified one and rejects the rest.

Four Postfix-image variables are still **read as fallbacks**, so one env file can drive both images through a switchover and pymta starts correctly against a file written for Postfix. The prefixed name always wins when both are set:

| read as a fallback | preferred |
|---|---|
| `MAX_INCOMING_EMAIL_SIZE` | `PYMTA_MAX_INCOMING_EMAIL_SIZE` |
| `MYHOSTNAME` | `PYMTA_SMTP_HOSTNAME` |
| `ENABLE_PROXY_PROTOCOL=haproxy` | `PYMTA_ENABLE_PROXY_PROTOCOL=true` (or `haproxy`) |
| `STARTTLS_CHAIN_FILES` | `PYMTA_TLS_CERT_FILE` + `PYMTA_TLS_KEY_FILE` |

Each one that is actually doing the work is named at startup with `event=legacy_setting_in_use` and the variable to set instead, so the fallback is a bridge rather than a resting place.

The last row is the only one that is not a rename. Postfix packs the key and the chain into one PEM; Python's `ssl` takes the two separately, so a bundle at `/path/chain.pem` becomes:

```yaml
PYMTA_TLS_CERT_FILE: /path/chain.pem
PYMTA_TLS_KEY_FILE: /path/chain.pem
```

The same path twice, which is what the fallback does for you. Two distinct files work too, and are what most certificate tooling emits. A comma-separated list carries over unchanged — see dual certificates below.

### Dual certificates (RSA + ECDSA)

Both TLS variables take a comma-separated list, paired by position:

```yaml
PYMTA_TLS_CERT_FILE: /tls/ecdsa.crt,/tls/rsa.crt
PYMTA_TLS_KEY_FILE:  /tls/ecdsa.key,/tls/rsa.key
```

OpenSSL keeps one certificate slot per key type and, at each handshake, presents whichever the client said it can verify. So one listener serves the smaller, faster ECDSA certificate to senders that support it and RSA to everything else, without SNI or a second port.

Order does not select the certificate — the client's advertised algorithms do. It only matters between two certificates of the *same* key type, where the later one takes the slot and is the one served; that is a configuration mistake pymta cannot detect, since it never parses the certificates.

Mismatched list lengths are refused at startup: the pairing is positional, so a cert would otherwise be loaded against another's key and OpenSSL's complaint names neither variable. An empty entry (a stray comma) is refused for the same reason a half-configured pair is — it would quietly become "STARTTLS off".

## Production checklist

The defaults are tuned for the dev stack. Five things to set before pymta faces the internet; each has a legitimate reason to differ in dev, so the process does not enforce them.

**1. Pick a supported topology, and isolate port 25.** There are exactly two:

| | PROXY protocol | `PYMTA_TRUSTED_PROXIES` | Client IP comes from |
|---|---|---|---|
| **Behind a load balancer** | `PYMTA_ENABLE_PROXY_PROTOCOL=true` | **strongly recommended**: the balancer's IPs/CIDRs | the PROXY header |
| **Directly exposed** | off | ignored | the TCP peer |

**A load balancer without PROXY protocol is not supported.** pymta would see only the balancer's address, so every session would bucket under one IP, and the per-source share would silently become a much lower global cap, and every message would be stamped with the balancer's own IP as the sender's, in `Received` and in the envelope the MDA stores. There is no setting for this topology and no fallback that makes it work.

An empty `PYMTA_TRUSTED_PROXIES` with PROXY protocol on means **every peer's header is trusted** — there is nothing left to filter on, exactly as if you had written `0.0.0.0/0`. It starts, with a `SECURITY:` warning on the first lines of the log, because deployments whose balancer addresses are not known at boot still have to run. In that mode the network isolation below is the *entire* trust boundary; name the balancer whenever you can.

The one thing pymta does enforce: with PROXY protocol on it never falls back to the TCP peer for `client_address`, so a header-less connection (a v2 `LOCAL` health check) reports no client rather than naming the balancer.

Network isolation has to come from the deployment. A PROXY header is trusted on the word of the peer that sent it, and it sets both the key for every per-IP cap and the `client_address` the MDA writes into `Received`. A peer that can open a TCP connection to port 25 directly (a container port published on a node, a second interface, a foothold on an internal network) can otherwise spread a forged source across the address space until only the global cap applies, and attribute its mail to any IP it names. Enforce the isolation in the NetworkPolicy or firewall as well as in the allowlist.

**2. `MDA_API_BASE_URL` must be `https://`.** The MTA→MDA channel is authenticated by a bearer token; over plaintext to a non-local host, anyone on the path can read it. pymta logs a startup warning when it sees `http://` pointing anywhere but localhost.

**3. `MDA_API_SECRET` should be at least 32 bytes of real entropy, and not the dev value.** It is an HS256 shared secret: short ones are brute-forceable offline from a single captured JWT, and `my-shared-secret-mda` is in the repo. pymta warns at startup below 32 bytes, and again if the secret is missing entirely.

**4. Set `PYMTA_METRICS_API_KEY`, and keep the metrics port off the interface that serves port 25.** `PYMTA_METRICS_BIND_HOST` defaults to `0.0.0.0` because the usual scrape paths (Prometheus hitting the pod IP, a compose port mapping) cannot reach a loopback-only listener.

Set the key. Scrape with `Authorization: Bearer <key>`, the same arrangement as the backend's `PROMETHEUS_API_KEY` (see `core.middlewares.PrometheusAuthMiddleware`), so one scrape config covers both. Most exporters ship unauthenticated and lean on network policy, which is why it is opt-in here too, but two things argue for turning it on. The exposition carries no addresses and no message content, but it does carry volumes, rejection reasons, breaker state, and the configured limits themselves, which tells an attacker how much traffic it takes to hit each cap. And the exposition server runs inside the SMTP process, threading one connection at a time, so an open port 9100 is a thread-exhaustion path to port 25 and not only a disclosure. Handlers carry a 10 s socket timeout to bound that. The key and a NetworkPolicy are what close it.

Where you cannot authenticate, restrict with a NetworkPolicy, or set `PYMTA_METRICS_BIND_HOST=127.0.0.1` / `PYMTA_METRICS_BIND_PORT=0`.

**5. Size the session-slot budget.** `PYMTA_MAX_CONCURRENT_DATA` sets both the memory ceiling and, at 3x, the session slots; `PYMTA_SESSION_TIMEOUT` bounds how long one connection can occupy one.

Every other timeout is re-armed by peer activity, so a peer that stays marginally active can hold a session open for far longer than `PYMTA_COMMAND_TIMEOUT` suggests. The session cap is the one bound that is not re-armed. It does not prevent a distributed attacker from occupying slots, but it means blocking one frees the slots instead of leaving them held until the process restarts, and it stops `PYMTA_SHUTDOWN_TIMEOUT` (25 s) from severing long-lived sessions on every rollout.

**There is no per-source connection flag to tighten.** Each source gets a share of the derived `PYMTA_MAX_SESSIONS_TOTAL` sized by how many sources are currently connected, so the limit relaxes when the server is quiet and tightens under contention. That matters most during a queue flush: after pymta downtime or a tripped MDA breaker, every sender with a backlog retries at full concurrency, and a fixed low cap would extend the outage by rejecting the senders trying to drain into you. A share does not, because it only shrinks when there are enough of them to shrink it.

Slot exhaustion on an *inbound* MTA delays mail rather than losing it: once the global cap is hit, new connections get `421` and close, and SMTP senders retry for days. Watch `pymta_sessions_active` and the upper buckets of `pymta_session_duration_seconds`; a healthy inbound MX has sessions measured in seconds.

Also review `PYMTA_DATA_TIMEOUT` against your real size cap; it sets a floor on how slow a legitimate sender may be. At the 10 MiB default, 300 s means a sender has to sustain roughly 35 kB/s. The dev defaults file disables the per-IP cap entirely, because all local load comes from loopback.

## Address normalisation is a cross-service contract

pymta lower-cases the domain and preserves the local-part's case; the MDA matches mailboxes on the exact `(local_part, domain)` tuple. RCPT-check and deliver send the same string, so the two always agree with each other. But a mailbox registered lower-case will get a `550` for a mixed-case local-part. The sender sees the rejection rather than losing the message silently. Recorded here so the two services do not drift apart.

# ST Messages MTA inbound

The MTA is in charge of receiving emails from the Internet and pushing them to the MDA and ultimately the users.

It only deals with inbound email and won't even send bounces by itself.

The MTA is entirely stateless and configured from env vars.

## Two implementations, same contract

This directory ships **two** implementations in parallel. Both expose the same SMTP behaviour to the outside world and speak the same MDA REST contract:

| | Postfix + milter (default) | pymta (pure-Python) |
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

## When to use which

Postfix is the production default. The pymta implementation is offered side-by-side so it can take over once parity is proven; it is easier to extend (no milter protocol, no C glue), gives us Prometheus metrics, and reduces the attack surface (no Postfix binary, no `libmilter`, no on-disk queue at all).

Switching production from one to the other only requires re-pointing the inbound public IP to the other container. The MDA back-end and the env vars are identical, except that PROXY-protocol passthrough is `ENABLE_PROXY_PROTOCOL=haproxy` on Postfix and `PYMTA_ENABLE_PROXY_PROTOCOL=true` on pymta.

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

## pymta-specific env vars

In addition to the shared `MDA_API_BASE_URL` / `MDA_API_SECRET` / `MDA_API_TIMEOUT` / `MAX_INCOMING_EMAIL_SIZE`, the pymta image reads:

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_HOSTNAME` | `$MYHOSTNAME`, else `mta-in` | Banner / Received-header host name |
| `PYMTA_IDENT` | `ESMTP` | Banner text after the hostname; kept version-less on purpose |
| `PYMTA_SMTP_HOST` / `PYMTA_SMTP_PORT` | `0.0.0.0` / `25` | SMTP listener bind |
| `PYMTA_LOG_LEVEL` | `INFO` | Root log level |
| `PYMTA_METRICS_HOST` / `PYMTA_METRICS_PORT` | `0.0.0.0` / `9100` | Prometheus endpoint (set port to 0 to disable) |
| `PYMTA_MAX_RECIPIENTS` | `100` | RCPT TO cap per envelope |
| `PYMTA_MAX_ENVELOPES_PER_CONNECTION` | `10` | Envelopes per TCP session |
| `PYMTA_HARD_ERROR_LIMIT` | `50` | 4xx/5xx replies before forcing 421 + disconnect |
| `PYMTA_MAX_RCPT_MISSES_PER_SESSION` | `10` | Unknown-mailbox lookups before 421 + disconnect |
| `PYMTA_MAX_SESSIONS_PER_IP` | `100` (0 = off) | Per-IP concurrent session cap |
| `PYMTA_MAX_SESSIONS_PER_IP_PER_MINUTE` | `600` (0 = off) | Per-IP new-session rate cap (fixed 60 s window) |
| `PYMTA_MAX_SESSIONS_TOTAL` | `1000` (0 = off) | Process-wide concurrent session cap |
| `PYMTA_COMMAND_TIMEOUT` | `120` | Idle timeout (s) between complete commands; re-armed by each accepted command |
| `PYMTA_MAX_SESSION_SECONDS` | `1800` (0 = off) | Wall-clock ceiling (s) on one TCP session, armed at connect and never re-armed |
| `PYMTA_DATA_TIMEOUT` | `300` | Hard DATA-phase deadline (s): 354 → last body byte → MDA deliver → reply. Nothing in a DATA phase outlives it |
| `MDA_API_JWT_TTL` | `MDA_API_TIMEOUT + 90` | Lifetime (s) of the JWT signed for each MDA call |
| `PYMTA_TRUSTED_PROXIES` | empty | Comma-separated IPs/CIDRs allowed to send a PROXY header. Strongly recommended when PROXY protocol is on: empty means *every* peer's header is trusted, and startup only warns. Ignored when PROXY protocol is off |
| `PYMTA_SHUTDOWN_TIMEOUT` | `25` | Drain deadline on SIGTERM before abandoning in-flight sessions (s) |
| `PYMTA_MDA_BREAKER_THRESHOLD` | `10` (0 = off) | Consecutive MDA failures before short-circuiting to 451 |
| `PYMTA_MDA_BREAKER_COOLDOWN` | `30` | Seconds the breaker stays open before probing the MDA again |
| `PYMTA_TLS_CERT_FILE` / `PYMTA_TLS_KEY_FILE` | empty | STARTTLS cert + key paths (empty = STARTTLS off) |
| `STARTTLS_CHAIN_FILES` | empty | Postfix-compatible fallback: comma-separated PEM bundle(s); first bundle wins when `PYMTA_TLS_*` is unset |
| `PYMTA_ENABLE_SMTPUTF8` | `true` | Advertise SMTPUTF8 in EHLO |
| `PYMTA_ENABLE_PROXY_PROTOCOL` | `false` | Enable PROXY-protocol v1/v2. The Postfix image uses its own `ENABLE_PROXY_PROTOCOL=haproxy`; pymta does not read that name, so the two can share an env file |
| `PYMTA_PROXY_PROTOCOL_TIMEOUT` | `5` | Seconds to wait for the PROXY header before dropping the connection |

Integer settings are range-checked at startup: those documented as `0 = off` accept zero, the rest reject it. `PYMTA_MAX_LOCAL_PART` and `PYMTA_MAX_DOMAIN` are **not** configurable. They are the RFC 5321 §4.5.3.1 constants (64 / 255 octets), and raising them would only widen the gap between what pymta accepts at RCPT and what the MDA can store.

## Production checklist

The defaults are tuned for the dev stack. Five things to set before pymta faces the internet; each has a legitimate reason to differ in dev, so the process does not enforce them.

**1. Pick a supported topology, and isolate port 25.** There are exactly two:

| | PROXY protocol | `PYMTA_TRUSTED_PROXIES` | Client IP comes from |
|---|---|---|---|
| **Behind a load balancer** | `PYMTA_ENABLE_PROXY_PROTOCOL=true` | **strongly recommended**: the balancer's IPs/CIDRs | the PROXY header |
| **Directly exposed** | off | ignored | the TCP peer |

**A load balancer without PROXY protocol is not supported.** pymta would see only the balancer's address, so every session would bucket under one IP (`PYMTA_MAX_SESSIONS_PER_IP` silently becomes a second, much lower global cap), and every message would be stamped with the balancer's own IP as the sender's, in `Received` and in the envelope the MDA stores. There is no setting for this topology and no fallback that makes it work.

An empty `PYMTA_TRUSTED_PROXIES` with PROXY protocol on means **every peer's header is trusted** — there is nothing left to filter on, exactly as if you had written `0.0.0.0/0`. It starts, with a `SECURITY:` warning on the first lines of the log, because deployments whose balancer addresses are not known at boot still have to run. In that mode the network isolation below is the *entire* trust boundary; name the balancer whenever you can.

The one thing pymta does enforce: with PROXY protocol on it never falls back to the TCP peer for `client_address`, so a header-less connection (a v2 `LOCAL` health check) reports no client rather than naming the balancer.

Network isolation has to come from the deployment. A PROXY header is trusted on the word of the peer that sent it, and it sets both the key for every per-IP cap and the `client_address` the MDA writes into `Received`. A peer that can open a TCP connection to port 25 directly (a container port published on a node, a second interface, a foothold on an internal network) can otherwise spread a forged source across the address space until only the global cap applies, and attribute its mail to any IP it names. Enforce the isolation in the NetworkPolicy or firewall as well as in the allowlist.

**2. `MDA_API_BASE_URL` must be `https://`.** The MTA→MDA channel is authenticated by a bearer token; over plaintext to a non-local host, anyone on the path can read it. pymta logs a startup warning when it sees `http://` pointing anywhere but localhost.

**3. `MDA_API_SECRET` should be at least 32 bytes of real entropy, and not the dev value.** It is an HS256 shared secret: short ones are brute-forceable offline from a single captured JWT, and `my-shared-secret-mda` is in the repo. pymta warns at startup below 32 bytes, and again if the secret is missing entirely.

**4. Keep the metrics port off the interface that serves port 25.** `PYMTA_METRICS_HOST` defaults to `0.0.0.0` because the usual scrape paths (Prometheus hitting the pod IP, a compose port mapping) cannot reach a loopback-only listener. The exposition carries no addresses and no message content, so what leaks is operational: volumes, rejection reasons, breaker state. Restrict it with a NetworkPolicy, or set `PYMTA_METRICS_HOST=127.0.0.1` / `PYMTA_METRICS_PORT=0` where you can.

**5. Size the session-slot budget.** `PYMTA_MAX_SESSIONS_TOTAL` slots are the contended resource, and `PYMTA_MAX_SESSION_SECONDS` bounds how long one connection can occupy one.

Every other timeout is re-armed by peer activity, so a peer that stays marginally active can hold a session open for far longer than `PYMTA_COMMAND_TIMEOUT` suggests. The session cap is the one bound that is not re-armed. It does not prevent a distributed attacker from occupying slots, but it means blocking one frees the slots instead of leaving them held until the process restarts, and it stops `PYMTA_SHUTDOWN_TIMEOUT` (25 s) from severing long-lived sessions on every rollout.

`PYMTA_MAX_SESSIONS_PER_IP_PER_MINUTE` does not constrain slot occupancy at any usable value: sustainable held slots per IP is `min(max_per_ip, rate_per_minute × session_minutes)`, so the concurrent cap binds first for any rate a real sender could tolerate. It defends against fast open/close churn (CPU, TLS handshakes, MDA recipient checks).

**Do not tighten `PYMTA_MAX_SESSIONS_PER_IP` far.** Postfix's `default_destination_concurrency_limit` is 20, so a single default-configured relay sending a backlog sits at a cap of 20 and gets 421s on its 21st connection; busy relays raise that figure. The worst case for a tight cap is a queue flush: after pymta downtime or a tripped MDA breaker, every sender with a backlog retries at full concurrency, and a low cap extends the outage by rejecting the senders trying to drain into you. The gain in return is small, since the number of source IPs an attacker needs scales only linearly with the cap. 100 is a reasonable default, and the shipping Postfix config disables per-client limits entirely (`smtpd_client_event_limit_exceptions = static:all`), so it is already a tightening.

Slot exhaustion on an *inbound* MTA delays mail rather than losing it: once the global cap is hit, new connections get `421` and close, and SMTP senders retry for days. Watch `pymta_sessions_active` and the upper buckets of `pymta_session_duration_seconds`; a healthy inbound MX has sessions measured in seconds. `PYMTA_MAX_SESSIONS_PER_IP_PER_MINUTE` refuses churn before the dialogue opens: the gate is acquired ahead of the `220` greeting, and TLS here is STARTTLS (an in-session command, not a handshake on accept), so a refused connection costs no asymmetric crypto and no MDA recipient check. What it still costs is the TCP accept, one protocol object, and the `421` write — small per connection, but paid inside this process. Only a cap upstream of it (firewall, load balancer, network ACL) drops the packet before that.

Also review `PYMTA_DATA_TIMEOUT` against your real `MAX_INCOMING_EMAIL_SIZE`; it sets a floor on how slow a legitimate sender may be. The dev defaults file disables the per-IP cap entirely, because all local load comes from loopback.

## Address normalisation is a cross-service contract

pymta lower-cases the domain and preserves the local-part's case; the MDA matches mailboxes on the exact `(local_part, domain)` tuple. RCPT-check and deliver send the same string, so the two always agree with each other. But a mailbox registered lower-case will get a `550` for a mixed-case local-part. The sender sees the rejection rather than losing the message silently. Recorded here so the two services do not drift apart.

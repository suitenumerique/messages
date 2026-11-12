# ST Messages MTA inbound

The MTA is in charge of receiving emails from the Internet and pushing them to the MDA and ultimately the users.

It only deals with inbound email and won't even send bounces by itself.

The MTA is entirely stateless and configured from env vars.

## Two implementations, same contract

This directory ships **two** implementations. pymta is the one to deploy.

> **The Postfix + milter image is deprecated.** It is kept for now and will be removed. It receives no further fixes: the behaviour described in this document is pymta's, and the Postfix image no longer matches it (sender address forms, MAIL/RCPT parameters, session limits, the `Received` trace, and more). Do not start a new deployment on it.

| | Postfix + milter (**deprecated**) | pymta |
|---|---|---|
| Compose service | `mta-in` (host port `8910`) | `mta-in-py` (host port `8920`) |
| Image | `Dockerfile` | `Dockerfile.pymta` |
| SMTP server | Postfix `smtpd` | `pymta.smtp_protocol`, on asyncio and the standard library |
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
| `400`, `413` with the MDA's own body (`"status": "error"`) | `554` | The message itself is unacceptable: unparseable or oversize. A retry sends the same bytes. |
| `400`, `413` with any other body | `451` | Something in front of the MDA answered (an ingress with a smaller body limit). Not a verdict on the message. |
| `415` | `451` | The MDA does not know the request form: an MDA older than this pymta, mid-rollout. Deploying the MDA clears it. |
| `207 Multi-Status` | `451` | Partial delivery. The MDA cannot ask us to retry only the failed recipients, so the whole envelope must be retried. That duplicates for the recipients already served, which is preferable to losing the rest. Requires per-recipient delivery to be idempotent on the MDA side. |
| `401`, `403`, `429`, `404`, any other 4xx | `451` | Secret rotation, `exp` clock skew, throttling, a bad route. Operational events, not verdicts on the mail. |
| `5xx`, timeout, transport error | `451` | Upstream is unhealthy; also counts toward the circuit breaker, except a 5xx with the MDA's own `"status": "error"` body, which is its answer about one message. |
| `200` with an unrecognised body | `451` | Not proof of delivery. |

Only 5xx and transport failures feed the circuit breaker. A `207` or a `401` is a complete answer from a healthy MDA, and so is a 500 about a message it could not deliver: counted, it would let one sender's messages defer everyone else's.

`check/` names **no** permanent statuses at all. Every entry above is a verdict on a *message*, and a recipient check carries none: a `400` or `413` there is a fault in the check request we built, so saying `554` would tell the sender a working address is permanently bad. The only permanent reply at RCPT TO comes from a `200` whose body says the mailbox does not exist.

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

- **Body**: for `check/`, an `application/json` document `{"addresses": [...]}`. For `deliver/`, one of two forms, which the MDA tells apart by Content-Type:
  - pymta sends `application/vnd.stmsg.mta-delivery`: the envelope (`sender`, `original_recipients`, `client_address`, `client_port`, `client_hostname`, `client_helo`, `size`, and how the message arrived: `protocol`, `tls_version`, `tls_cipher`, `server_name`) as one line of JSON, a newline, then the full RFC 5322 message.
  - the Postfix milter sends `message/rfc822`: the message alone, with the same envelope fields as JWT claims. An HTTP header caps that form (gunicorn refuses one over 8190 bytes, which about 100 recipients exceed), which is why pymta moved the envelope into the body.
- **Authorization**: `Bearer <jwt>` where the JWT is signed HS256 with `env.MDA_API_SECRET` and carries:
  - `exp`: `env.MDA_API_JWT_TTL` seconds from issuance, anchored in UTC. It has to cover the whole request *plus* clock skew against the MDA.
  - `body_hash`: `sha256(body).hexdigest()`, which binds the token to the exact bytes posted, so a captured token cannot be reused to send *different* content. The MDA tracks no nonce, so replaying the same token with the same body until `exp` is not prevented; the two claims bound a captured token together, one in content and one in time.
  - `mta_session`: the SMTP session id, so the MDA's log lines join up with pymta's.
  - Plus, in the milter's `deliver/` form only, the envelope fields. In pymta's form they are in the body, which `body_hash` covers.

Deploy the MDA before a pymta that sends the enveloped form. An older MDA answers it `415`, which pymta treats as a deferral, so the wrong order delays mail without bouncing it.
- **Response**: see the outcome table above.

**The trace header.** pymta adds nothing to a message. The MDA writes the `Received` header for this hop, on top of the `Return-Path` it already writes, from what pymta sends:

```text
Return-Path: <alice@sender.example>
Received: from mx.sender.example ([203.0.113.9])
	by mx1.example.org with ESMTPS id 9bf92de8 tls TLS_AES_256_GCM_SHA384 (TLSv1.3);
	Wed, 11 Nov 2026 15:04:05 +0000
```

- `with` says whether the session was encrypted: `ESMTP` in clear, `ESMTPS` over TLS (RFC 3848), `UTF8SMTP` / `UTF8SMTPS` when the sender declared `SMTPUTF8` (RFC 6531).
- `tls` names the cipher suite (RFC 8314 §4.3). The protocol version has no standard clause and goes in a comment, as Postfix writes it; `(TLSv1)` or `(TLSv1.1)` there is an old handshake, possible only with `PYMTA_TLS_MIN_VERSION` lowered.
- `id` is the session id of every pymta log line about this message.
- `by` is `PYMTA_SMTP_HOSTNAME`.
- There is no `for` clause: one delivery to several recipients is stored once, and a `for` would also show Bcc recipients to each other.

One writer, in the MDA, for three reasons: the header and `Return-Path` stay in a fixed order above everything the sender sent, which is what the MDA's spam rules and `Authentication-Results` reader rely on; pymta does not copy a 35 MiB body to put a line in front of it; and pymta adds nothing of its own to the message (it only removes the dot-stuffing and rewrites bare line endings, see *Line endings* below). The same facts are kept in the queued message's envelope and given to rspamd as `TLS-Version` / `TLS-Cipher`.

`make test-mta-in-py-e2e` checks this over a real SMTP session, pymta and the backend running together.

**Redelivery and duplicates.** There is no queue here, so any `4xx` at the end of DATA has the sender resend the whole message later: after a partial delivery (`207`), an MDA timeout, or a shutdown that cut the session. The MDA drops a redelivered copy by its `Message-ID`, per mailbox, including when the first copy is still in its inbound queue. A message that has **no `Message-ID`** is not recognised and is stored again each time. This is accepted: such mail is rare, and a duplicate is not a loss. `core/tests/mda/test_inbound.py::test_without_a_message_id_a_redelivery_is_a_second_message` pins it.

## Which one to use

**Use pymta.** It is the implementation being developed. Postfix + milter is deprecated: it still runs and the shared tests still run against it, but it gets no further fixes and will be removed.

The case for pymta is smaller attack surface and better operability: no Postfix binary, no `libmilter` C glue, no on-disk queue, a distroless image that runs with a read-only root filesystem and no writable mount at all, Prometheus metrics, and per-IP admission control the Postfix config disables. It is also simply easier to change, which is why the security fixes in this directory landed there first.

Two things pymta does that Postfix does not, worth knowing before switching: it enforces a per-IP concurrent session cap (the shipped Postfix config turns per-client limits off entirely), and it holds each message in memory rather than spooling to disk, so concurrency times message size is a real memory ceiling. Both are covered below.

Switching production from one to the other only requires re-pointing the inbound public IP to the other container: the MDA back-end is unchanged, and pymta reads the Postfix image's variable names as fallbacks so an existing env file keeps working (see [Naming](#naming)).

## Running

```bash
# Default Postfix-based service
make test-mta-in
make lint-mta-in

# Pure-Python service
make test-mta-in-py
make lint-mta-in-py
make fuzz-mta-in-py            # Hypothesis suite, about a minute
make fuzz-mta-in-py-intensive  # the same with 10x the examples, about 10 minutes
make test-mta-in-py-e2e        # pymta and the real backend together, over SMTP
```

`make test-mta-in-py` fails under 100% coverage of `src/pymta`, lines and branches. A branch that no test can reach is removed, not excused.

### How pymta is laid out

Its only runtime dependencies are `httpx` (the MDA client), `PyJWT`, `idna` and `prometheus-client`. The SMTP side is ours, on asyncio:

| Module | What it does |
|---|---|
| `wire.py` | The receive buffer of one connection: lines, fixed-size reads, back-pressure |
| `command.py` | Parses a command line. Pure functions, no I/O |
| `address.py` | Decides whether an envelope address is one we accept |
| `proxy_protocol.py` | Reads a PROXY v1 or v2 header |
| `smtp_protocol.py` | The dialogue of one connection: admission, command order, DATA, STARTTLS, timeouts |
| `session_log.py` | What is logged about a session: the `reject` lines of the protocol and `session_closed` |
| `handler.py` | What to do with a sender, a recipient, a message: the MDA calls and the per-session budgets |
| `mda_async.py` | The MDA client, its signing and its circuit breaker |
| `limits.py` | Session and DATA slots, per source and in total |
| `controller.py`, `server.py` | The listener, its TLS context, startup and shutdown |

Tests reach the server through a real socket (`tests/harness.py`), so they do not depend on how it is built.

pymta first ran on [aiosmtpd](https://github.com/aio-libs/aiosmtpd) and `smtp_protocol.py` and `proxy_protocol.py` are adapted from it. aiosmtpd is Copyright 2014-2021 The aiosmtpd Developers and licensed under Apache-2.0; see [`NOTICE`](NOTICE) and [`LICENSE.aiosmtpd`](LICENSE.aiosmtpd), which are also shipped in the image.

### Fuzzing

`tests/test_*_fuzz.py` are property tests, marked `fuzz` and left out of `make test-mta-in-py`. CI runs them as its own job (`fuzz-mta-in-py`), with fewer examples on a pull request. `FUZZ_EXAMPLES=n make fuzz-mta-in-py` sets the count.

| File | What it throws at pymta | What must hold |
|---|---|---|
| `test_address_fuzz.py` | Arbitrary and address-shaped text, undecodable bytes | The validator returns a clean address or raises `AddressError`, never anything else; what it accepts is storable, loggable and validates to itself; a sender comes out in one canonical spelling, and reads back the same after a second trip through the SMTP parser |
| `test_limits_fuzz.py` | Sessions of eight sources arriving and leaving in any order, on a gate of 1 to 24 slots | No slot is lost or overdrawn; a slot is only taken back from the largest source, for one that holds at least two fewer, and cannot be taken back in return |
| `test_wire_fuzz.py` | Bodies full of CR, LF and dots; peer text in log fields; MAIL/RCPT parameters; arbitrary MDA responses | No bare line ending survives and dot-stuffing is undone; a log line carries exactly the fields it was given; ignoring a parameter never changes the address; any MDA response becomes a result |
| `test_protocol_fuzz.py` | Whole sessions against an in-process server: valid commands, malformed ones, raw bytes, with an MDA whose answers are part of the example | No exception of ours; one reply per line; every 4xx/5xx has its log line and the session its summary; the session slot is given back; what reaches the MDA is clean; `250` after DATA only if the MDA took the message. Every write is cut into TCP segments at random points |
| same file | One message with a body of CR, LF, dots, NULs and long runs, against a small size limit | The reply and the stored bytes match an independent statement of the rules (a differential test of the DATA loop) |
| same file | Two to five sessions at once on a server with one to four slots | Refused at the cap, cut or served: every session and DATA slot comes back, and nothing raises |
| same file | A whole session in one write, before or after the greeting, without waiting for any reply | Refused for synchronization; nothing is ever delivered, and no `354` is given to a body already sent |
| same file | PROXY v1 and v2 headers, valid, malformed, truncated, random | Accepted or refused without an exception; the client address is the one the header named, or nobody |

A failure prints the smallest input that reproduces it, and Hypothesis stores it in `.hypothesis/` so the next run replays it first. Turn it into a plain test in the matching `test_*.py` once fixed.

`tests/test_known_defects.py` is the other half: defects published against other SMTP servers (aiosmtpd, which this one ran on at first, Postfix, Exim, OpenSMTPD, Haraka, Mailpit and others), replayed here. Each test names its source. It runs with the normal suite. Add to it when a new advisory describes a class of bug this server could have.

The shared test suite under `tests/` runs against both via the `MTA_HOST` / `MTA_PORT` env vars. A few tests assert implementation-specific behaviour and skip on the other impl (e.g. `tests/test_metrics.py` is pymta-only; the strict NUL-byte rejection test skips on Postfix). The fixtures `mta_impl`, `mta_address`, and `mta_metrics_url` in `tests/conftest.py` are how a test sees which impl it is running against.

## Environment variables

Both images are stateless and configured entirely from the environment. Each section below is the **complete** list its image reads; nothing else in the environment reaches either process. `deploy/env/mta-in.defaults` is loaded by both compose services, with `deploy/env/mta-in-py.defaults` layered on top for pymta.

### Postfix + milter (`mta-in`)

Most of this image's behaviour lives in `etc/main.cf` rather than in the environment; `entrypoint.sh` translates only the variables below into `main.cf` directives before starting Postfix.

| Variable | Default | Purpose |
|---|---|---|
| `MDA_API_BASE_URL` | none — **required** | Base URL of the MDA REST API, with trailing slash. Unset, every MDA call raises and the milter tempfails, so all mail defers |
| `MDA_API_SECRET` | none — **required** | HS256 shared secret for the MDA bearer token. Unset, it fails the same way. This image has no startup validation of either; pymta refuses to start without the secret |
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
| `MDA_API_SECRET` | none — **required** | HS256 shared secret for the MDA bearer token. pymta refuses to start without it, and warns under 32 bytes |
| `MDA_API_TIMEOUT` | `30` | HTTP timeout (s) for MDA calls |
| `MDA_API_JWT_TTL` | `MDA_API_TIMEOUT + 90` | Lifetime (s) of the JWT signed for each MDA call. Must cover the request plus clock skew against the MDA |
| `MDA_BREAKER_THRESHOLD` | `10` (0 = off) | Consecutive MDA failures before short-circuiting to 451. pymta-only; the milter has no breaker and ignores it |
| `MDA_BREAKER_COOLDOWN` | `30` | Seconds the breaker stays open before probing the MDA again. After it, one call goes through as a probe while the others are still answered 451; its success closes the breaker, its failure starts another cooldown. A timeout waiting for a free connection in pymta's own HTTP pool is not counted as an MDA failure. Must be ≥ 1: a zero would reopen the breaker to the very next call, which neuters it rather than disabling it — use the threshold for that |

**SMTP listener and identity**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_SMTP_BIND_HOST` / `PYMTA_SMTP_BIND_PORT` | `0.0.0.0` / `25` | Where the SMTP listener binds |
| `PYMTA_SMTP_HOSTNAME` | `mta-in` | What it calls itself: banner and `Received`-header host name |
| `PYMTA_SMTP_IDENT` | `ESMTP` | Banner text after the hostname; kept version-less on purpose |
| `PYMTA_ENABLE_SMTPUTF8` | `true` | Advertise SMTPUTF8 in EHLO |
| `PYMTA_POSTMASTER_ADDRESS` | empty | Mailbox that receives `RCPT TO:<postmaster>`, the bare form RFC 5321 §4.5.1 requires accepting. The recipient is rewritten to this address, then checked with the MDA like any other. Empty: the bare form is refused with `501`. `postmaster@<domain>` is an ordinary address either way |
| `PYMTA_RECIPIENT_DELIMITER` | `+` | Separates a mailbox name from a tag (`user+tag@domain`). Used only so that an address in `PYMTA_BLOCKED_RECIPIENTS` is also blocked under any tag; which mailbox a tagged address reaches is the MDA's decision. Empty disables it. Keep it equal to the delimiter the MDA resolves |
| `PYMTA_REQUIRE_FQDN_SENDER` | `true` | Refuse a MAIL FROM whose domain is not fully qualified: a single label such as `root@localhost` (`504 5.5.2`), or an address literal such as `user@[192.0.2.1]` (`501 5.1.3`). `false` accepts both. The null sender `<>` is not affected |

**Message and session limits**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_MAX_INCOMING_EMAIL_SIZE` | `10485760` (10 MiB) | Total message size cap, advertised as the ESMTP `SIZE` value. **Must equal the backend's `MAX_INCOMING_EMAIL_SIZE`**: the two services are configured separately and nothing checks one against the other. With pymta's higher, a message between the two is received in full, refused `413` by the MDA and bounced `554`; with pymta's lower, mail the MDA would store is refused `552`. Only the defaults agree on their own. Raising it also raises the memory needed (see `PYMTA_MAX_CONCURRENT_DATA`) and what `PYMTA_DATA_TIMEOUT` asks of a sender's bandwidth |
| `PYMTA_MAX_RECIPIENTS_PER_ENVELOPE` | `100` | RCPT TO cap per envelope |
| `PYMTA_MAX_ENVELOPES_PER_SESSION` | `100` | Messages per TCP session. The MAIL that would open the next one is answered `421` and the connection closed, before anything is uploaded. A sender that reuses its connections has that message wait for its next queue run, so keep it high enough to be rare |
| `PYMTA_MAX_ERRORS_PER_SESSION` | `50` | 4xx/5xx replies before forcing 421 + disconnect (Postfix's `smtpd_hard_error_limit`). Counts every such reply, including the ones given to a malformed command before the handler is asked. `DATA` and `QUIT` are still answered past it, so a transaction whose recipients were accepted can finish |
| `PYMTA_MAX_RCPT_MISSES_PER_SESSION` | `10` | Unknown-mailbox lookups before 421 + disconnect. Under stress (see `PYMTA_COMMAND_TIMEOUT`) it is 2, and the error budget above is 5: the sessions collecting refusals are the first to give their slot back |
| `PYMTA_MAX_CONCURRENT_DATA` | `40` | Messages held in memory at once. **The bound on memory**, see below. `0` disables far more than the memory bound — see the warning under "The session cap is derived" |
| `PYMTA_MAX_SHARE_PER_SOURCE` | `0.1` | Most of the session slots, and of the message slots, one source (an IPv4 address or an IPv6 /64) may hold, as a fraction above 0 and up to 1. See "What one source may hold" below |
| `PYMTA_MAX_LINE_LENGTH` | `65536` | Longest single line accepted, in octets, line ending excluded. The RFC-strict 998 permanently rejects mail Postfix accepts |

**Timeouts**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_COMMAND_TIMEOUT` | `300` | Idle timeout (s) between complete commands; counted from our last reply. 300 is the minimum RFC 5321 §4.5.3.2.7 asks for. Once four fifths of the session slots are taken, a session waiting for a command gets 10 s instead (Postfix's stress value), inside a transaction too and including the sessions that were already waiting, so the ones doing nothing are the first to free their slot; a command that is running always has the full value |
| `PYMTA_DATA_TIMEOUT` | `300` | Hard DATA-phase deadline (s): 354 → last body byte → MDA deliver → reply. Nothing in a DATA phase outlives it |
| `PYMTA_SESSION_TIMEOUT` | `1800` (0 = off) | Wall-clock ceiling (s) on one TCP session, armed at connect and never re-armed. A peer that is not reading our replies is dropped without the 421, and any close it does not take is forced after 5 s, so the slot always comes back |
| `PYMTA_SHUTDOWN_TIMEOUT` | `25` | Drain deadline on SIGTERM before abandoning in-flight sessions (s). Idle sessions are answered `421` and closed at once; a transaction already open may finish, and its session gets the `421` on the next command that would start new work |
| `PYMTA_PROXY_PROTOCOL_TIMEOUT` | `5` | Seconds to wait for the PROXY header before dropping the connection |

**PROXY protocol and STARTTLS**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_ENABLE_PROXY_PROTOCOL` | `false` | Enable PROXY-protocol v1/v2. `haproxy` is an alias for `true` — it is the only protocol postscreen defines and the only one implemented here — so both names take either spelling |
| `PYMTA_BLOCKED_NETWORKS` | empty | Comma-separated IPs/CIDRs refused at connect time with `554 5.7.1 Access denied`, before the banner. Matched against the **client** address: the PROXY-header source when PROXY protocol is on, the TCP peer otherwise. Nothing to do with `PYMTA_TRUSTED_PROXIES`. Permanent rather than a 421 on purpose, since a deferral invites the retries you are trying to shed; a block covering a legitimate sender therefore bounces their mail, so keep the ranges narrow |
| `PYMTA_TRUSTED_PROXIES` | empty | Comma-separated IPs/CIDRs allowed to send a PROXY header. Required when PROXY protocol is on: pymta refuses to start without it. `0.0.0.0/0,::/0` trusts every peer, with a warning at startup. Ignored when PROXY protocol is off |
| `PYMTA_TLS_CERT_FILE` / `PYMTA_TLS_KEY_FILE` | empty | STARTTLS cert + key paths, comma-separated for dual certificates (see below). **Both or neither** — one alone is refused at startup, since it would serve plaintext and simply not advertise STARTTLS. Both empty disables STARTTLS deliberately. A single PEM holding both the key and the chain (the Postfix layout) is given to *both*: `ssl.load_cert_chain` reads each from the same file. The files are read again on SIGHUP, so a renewal needs no restart (see [Certificate renewal](#certificate-renewal)) |
| `PYMTA_REQUIRE_TLS` | `false` | Answer `530` to every command but EHLO, STARTTLS, NOOP and QUIT until the session has done STARTTLS. For a listener whose senders are known. On a public MX it refuses every sender that cannot do TLS, or that fails the handshake and retries in clear: publish MTA-STS or DANE for the domain instead, and the senders that support them enforce TLS themselves. Refuses to start without a certificate. Refusals are `reject` lines with `reason=tls_required` |
| `PYMTA_TLS_MIN_VERSION` | `1.2` | Oldest TLS version accepted: `1.0`, `1.1`, `1.2` or `1.3`. A sender limited to 1.0 or 1.1 fails the handshake at the default and shows up as `tls_error=` on its `session_closed` line; it then retries in clear or gives up. Setting `1.0` or `1.1` also lowers OpenSSL to security level 0 for every handshake on the listener, because those versions sign the handshake with SHA-1 |

**Metrics, logging, container**

| Variable | Default | Purpose |
|---|---|---|
| `PYMTA_METRICS_BIND_HOST` / `PYMTA_METRICS_BIND_PORT` | `0.0.0.0` / `9100` | Prometheus endpoint (set port to 0 to disable) |
| `PYMTA_METRICS_API_KEY` | empty | Bearer token required to scrape `/metrics`. Empty = unauthenticated, with a `metrics_unauthenticated` warning at startup. Same opt-in semantics as the backend's `PROMETHEUS_API_KEY` |
| `PYMTA_LOG_LEVEL` | `INFO` | Root log level: `CRITICAL`, `ERROR`, `WARNING`, `INFO` or `DEBUG` |
| `PYMTA_LOG_VERBOSE_LIBRARIES` | `false` | Let httpx/httpcore/asyncio log too. Debugging only: every MDA request is traced, headers included |
| `PYMTA_LOG_SESSIONS` | `true` | One `session_closed` line per connection at INFO (see [Tracing a message](#tracing-a-message-that-never-arrived)). `false` drops it to DEBUG, for a connection flood |
| `EXEC_CMD` | `false` | Start pymta in the background, then run the container command (used by `make test-mta-in-py`) |
| `EXEC_CMD_ONLY` | `false` | Skip startup entirely and exec the container command (used by `make lint-mta-in-py`) |

Every value is checked at startup and a bad one stops the process rather than being quietly ignored: booleans take `1/true/yes/on` or `0/false/no/off` and nothing else, integers must parse and clear their minimum (those marked `0 = off` accept zero, the rest reject it), `PYMTA_LOG_LEVEL` must name a real level, and `PYMTA_TRUSTED_PROXIES` must parse as IPs/CIDRs. Reading a typo as its opposite is how a security toggle ends up off in production, so nothing here has a silent fallback.

The address length caps are **not** configurable. They are the RFC 5321 §4.5.3.1 constants: 64 octets for the local-part and 255 for the domain (63 per label). Domain lengths are measured on the A-label (punycode) form DNS stores, not on the UTF-8 an SMTPUTF8 peer sends, and no IDNA validity rule is enforced on top. The local-part cap applies to RCPT only, where raising it would only widen the gap between what pymta accepts and what the MDA can store. MAIL FROM gets no local-part cap and no 256-octet path cap either: §4.5.3.1 asks to avoid these limits where possible, Postfix enforced neither, and a forwarder's SRS-wrapped return path (`SRS0=hash=tt=domain=user@forwarder`) routinely carries a local-part longer than 64 octets. Refusing it would bounce legitimately forwarded mail. The MAIL command-line limit (below) is what bounds it.

Two more differences between the two commands. `%` is accepted in a MAIL FROM local-part, where VERP return paths use it (`bounce-user%40example.org@esp.example`), and refused on RCPT TO, where it is the old percent-routing relay form. A sender domain with a single label (`root@localhost`) is refused unless `PYMTA_REQUIRE_FQDN_SENDER=false`; a recipient domain is whatever the MDA knows. MAIL FROM also accepts a quoted local-part (`"a b"@example.org`, with the spaces, `@` and dot placements only valid in quotes). An address literal as sender domain (`user@[192.0.2.1]`, `user@[IPv6:2001:db8::1]`) is valid in RFC 5321 but is not a fully-qualified domain, and nothing can answer it through an MX: it is refused like a single-label domain, and accepted only with `PYMTA_REQUIRE_FQDN_SENDER=false`. The sender is forwarded in one canonical spelling, quoted when its content needs it and only then, whatever the peer wrote: `"alice"@x` becomes `alice@x`, and `a..b@x`, which some senders do write, becomes `"a..b"@x`. Outside quotes a local-part is atoms and dots: a space, a tab, a backslash or an RFC 5322 comment (`alice(home)@x`) is refused. RCPT TO accepts none of the quoted forms, except quotes the content does not need (`"bob"@x` is `bob@x`): a recipient names a mailbox of ours and none has such a name. A source route (`<@a,@b:user@c>`) is accepted and ignored, as RFC 5321 §4.1.1.3 asks. The angle brackets are optional, but one that is opened has to be closed. A domain's ASCII characters are limited to letters, digits, `-`, `_` and `.`. Each refusal is a `reject` line with `verb=mail` or `verb=rcpt` and its `reason=`.

**Line endings.** No bare LF or CR is forwarded: each becomes CRLF, in headers and body alike, as Postfix's `smtpd_forbid_bare_newline = normalize` does, and a dot that followed a bare LF is unstuffed. One rule, no guess about what the sender meant. A bare LF inside a header therefore becomes a line break (`Subject: a<LF>X: b` is stored as two headers); what is guaranteed is that the MDA, rspamd and every later reader see the same lines. The rewrite is done line by line as the message is read, and `PYMTA_MAX_LINE_LENGTH` and `PYMTA_MAX_INCOMING_EMAIL_SIZE` apply to the result.

**Waiting for replies.** A peer that sends before it is asked is answered `554 5.5.0 Error: SMTP protocol synchronization` and disconnected, as Postfix does: bytes before the greeting, or already waiting behind `EHLO`/`HELO`, `STARTTLS` or `DATA` (a body sent before its `354`). Those are the points where what follows changes meaning, and where smuggled commands, HTTP requests aimed at port 25 and scripts that ignore replies show up. MAIL and RCPT sent together, or QUIT sent right behind a message, are accepted. Logged as a `reject` line with `reason=protocol_synchronization`.

**MAIL and RCPT parameters.** `SIZE=`, `BODY=` and `SMTPUTF8` are honoured. `AUTH=`, `RET=` and `ENVID=` on MAIL, and `NOTIFY=` and `ORCPT=` on RCPT, are accepted and ignored: DSN and AUTH are not offered, but senders send these anyway, the Postfix image accepts them, and nothing here would act on them (this server sends no DSN and relays nowhere). They are counted in `pymta_ignored_parameters_total{parameter}` and named in `ignored_parameters=` on `session_closed`. Any other parameter is answered `555`, as Postfix does, including the ones that ask for a behaviour (`REQUIRETLS`, `BY=`, `HOLDFOR=`).

A HELO name with control characters or bytes that are not UTF-8 does not refuse the session: the offending characters are forwarded to the MDA as `?`, and `session_closed` keeps what was sent, escaped, in `helo=`.

Two more limits are fixed in the protocol: a 512-octet command line (§4.5.3.1.4) and 5 lines per session that are not a command (an unknown verb, an empty line, bytes that are not ASCII) before it hangs up. After EHLO the MAIL line gets 548 octets, 26 more for `SIZE=` and 10 for `SMTPUTF8`. The 998-octet line length of §4.5.3.1.6 (1000 with its CRLF) is the one that is configurable: `PYMTA_MAX_LINE_LENGTH` replaces it. A line of a message ends at its LF, bare or not, so a message with LF-only line endings is not one long line. That value is the limit of the receive buffer, so an endless line is refused rather than buffered, and it bounds DATA lines only: commands stay under the separate command-line cap however high it is set. A MAIL or RCPT parameter given twice is a syntax error. STARTTLS is offered and not required unless `PYMTA_REQUIRE_TLS` is set, because refusing a sender that does not offer it loses mail rather than protecting it on a public MX. AUTH is answered `502` outright.

### Crisis toggles

Five settings exist to be changed while something is going wrong. Changing one is a **restart**: set the variable and roll the process. The SIGTERM drain below makes that graceful (new connections get `421` and go to another MX, open transactions finish), so a toggle costs seconds, not mail.

| Variable | Effect |
|---|---|
| `PYMTA_DEFER_ALL` | `451` at every RCPT, and the MDA is not consulted at all |
| `PYMTA_BLOCKED_NETWORKS` | `554` at connect for these client IPs/CIDRs |
| `PYMTA_BLOCKED_SENDER_DOMAINS` | `554` at MAIL FROM, matched case-insensitively on the domain |
| `PYMTA_BLOCKED_RECIPIENTS` | `550` at RCPT TO, full addresses, tagged forms included (`victim@` also blocks `victim+x@`), case-insensitive |
| `PYMTA_LOG_SESSIONS` | `false` moves the per-connection `session_closed` line from INFO to DEBUG, when a flood of connections is writing the log bill |

No setting is re-read while the process runs. A running process cannot be given new environment variables, so a reload from the environment could only find the values it started with.

**Future idea, not implemented:** apply these toggles without a restart by reading them on SIGHUP from a mounted file (a ConfigMap mounted as a directory, one file per setting, updates in place and needs no parsing). The code reads these settings at each use, so only the reading side would have to be added.

#### Certificate renewal

SIGHUP does one thing: it reads the certificate and key files again, from the same paths. Connections accepted afterwards get the new certificate; if the files do not load, `tls_reload_rejected` is logged and the previous certificate stays in use. Have whatever renews the certificate send the signal (a cert-manager secret mounted as a volume updates in place like a ConfigMap).

At startup and after each reload, one `tls_certificate` line per file gives `not_after=` and `days_left=`, and `pymta_tls_certificate_not_after_seconds{file=...}` exports the expiry. Alert on `pymta_tls_certificate_not_after_seconds - time() < 14 * 86400`: an expired certificate only shows up otherwise as `closed_by=tls_failed` from the senders that verify it.

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

There is no setting to drain a node: SIGTERM is the only way in, and a node that should stay out of rotation is one that is not running.

### Where the defaults come from

Each limit is set against what Postfix does for the same thing, since the two run side by side and a switchover should not change who gets served. `postconf -d` on the shipping image, versus what we ship:

| | Postfix default | Our `main.cf` | pymta default |
|---|---|---|---|
| Per-IP concurrent sessions | `smtpd_client_connection_count_limit` = 50 | **disabled** (`smtpd_client_event_limit_exceptions = static:all`) | a share of the total, no flag |
| Per-IP session rate | `smtpd_client_connection_rate_limit` = 0 (off) | disabled | none, as Postfix |
| Global concurrent sessions | `default_process_limit` = 100 processes | 100 | 120, derived from a 40-message DATA cap |
| Recipients per envelope | `smtpd_recipient_limit` = 1000 | 100 | 100 |
| Errors per session | `smtpd_hard_error_limit` = 20 | 50 | 50 |
| Command idle timeout | `smtpd_timeout` = 300 s | 300 s | 300 s |
| Line length | `line_length_limit` = 2048 (wraps output) | 2048 | 65536 (accepts) |

Two need explaining. Per-IP concurrency is a tightening, not a loosening: the shipping Postfix config turns per-client limits off entirely, so any per-source ceiling at all is stricter than production is today. In pymta a source's ceiling is its share of `PYMTA_MAX_SESSIONS_TOTAL`: `PYMTA_MAX_SHARE_PER_SOURCE` of it (0.1, so 12 of 120), and less once more than ten sources are present. Read the queue-flush note in the checklist below before lowering the total. Global concurrency is 120 sessions against Postfix's 100 processes, which is reasonable for one async process, but it sets the memory ceiling:

> **`PYMTA_MAX_CONCURRENT_DATA` (40) is the bound on memory.** Each message is held in RAM with no spool file, so messages in flight times the size cap is the heap. The session caps do not bound it: a connection costs a few kB until it says DATA.
>
> Size it against the memory you gave the container. A message peaks at about **2.2x** its size — the received blocks are joined into one buffer at the end of DATA, which needs a second copy for that moment, and freed arenas are not returned promptly. So:
>
> ```text
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
> **A source is an IPv4 address or an IPv6 /64.** A /64 is what one site or one host is assigned, so counting IPv6 by full address would give a single machine an unlimited number of sources.
>
> **What one source may hold** is the smaller of two bounds, and never less than one slot:
>
> ```text
> share = max(1, min(slots x PYMTA_MAX_SHARE_PER_SOURCE, slots / (sources present + 1)))
> ```
>
> | sources present | each may hold of 120 sessions | of 40 messages |
> |---|---|---|
> | 1 to 9 | 12 | 4 |
> | 11 | 10 | 3 |
> | 19 | 6 | 2 |
> | 39 | 3 | 1 |
>
> The fraction decides how many sources it takes to fill the server: at least one over it, so ten at the default of 0.1 (eleven in practice). The equal share takes over once more sources than that are present.
>
> The share is checked when a slot is asked for, so sources that arrived first can hold more than a share that has shrunk since. When every session slot is taken and a source under its share connects, a slot is taken back from the source that holds the most over its share: its session idle the longest is answered `421 4.4.2 Server busy, closing idle connection` and closed, and the newcomer is greeted (`pymta_disconnects_421_total{reason="reclaimed"}`, `closed_by=reclaimed`). Only a session with no command and no transaction in progress is closed for this, so a sender loses a connection it was not using and never a message. If there is none, the newcomer is answered 421. Message slots are never taken back; instead, once four fifths of them are taken, a message has to arrive at 500 octets a second or more (Postfix's `smtpd_min_data_rate`), checked every 10 s, or its session is answered `421 4.4.2 Message received too slowly` (`reason="data_timeout"`).
>
> Sessions that hold a slot and wait for a command more than 10 s are also closed while the server is above four fifths full, whether or not they have a transaction open, and no session lasts longer than `PYMTA_SESSION_TIMEOUT` (a message being received or delivered at that moment is finished first).
>
> The cost of a low fraction is on a large sender that comes from few addresses: beyond its share it gets 421 at connect or 451 at DATA, and retries. `pymta_connections_total{result="rejected_per_ip"}` shows it; raise the fraction if a sender you want is in there.
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
| `PYMTA_MAX_SESSIONS_TOTAL` (derived) and the per-source share | ✓ | `pymta_connections_total{result="rejected_global\|rejected_per_ip"}`; `pymta_disconnects_421_total{reason="reclaimed"}` for the idle sessions closed to let a new source in |
| `PYMTA_MAX_CONCURRENT_DATA` and its per-source share | ✓ | `pymta_data_phases_active`, and `reason="max_concurrent_data"` |
| `PYMTA_SESSION_TIMEOUT` | ✓ | `reason="session_timeout"` |
| `PYMTA_DATA_TIMEOUT` | ✓ | `reason="data_timeout"`, plus `pymta_disconnects_421_total` when the message was still being received. Also counts a message cut for arriving too slowly while message slots are scarce |
| `PYMTA_COMMAND_TIMEOUT` | ✓ | `reason="command_timeout"`, plus `pymta_disconnects_421_total`. The idle peer is answered 421 before the socket is closed, so it can tell a policy timeout from a network fault |
| stress mode (4/5 of `max_sessions_total`) | — | `pymta_under_stress` is 1 while sessions waiting for a command get the 10 s timeout; `pymta_disconnects_421_total{reason="command_timeout"}` counts the sessions it closes |
| `MDA_BREAKER_THRESHOLD` / `_COOLDOWN` | — | `pymta_mda_breaker_open` is 1 while open; `pymta_mda_request_duration_seconds{result="breaker_open"}` counts the calls it shed |

`pymta_sessions_abandoned_total` counts sessions cut by the `PYMTA_SHUTDOWN_TIMEOUT` drain deadline — non-zero on every rollout means the deadline is too short for your traffic.

### Tracing a message that never arrived

**The rule this log is held to.** A message that does not reach the MDA must be explainable from the log at the default level:

1. Every 4xx or 5xx sent in answer to a command writes one INFO line (`reject`, `message_deferred`, `message_rejected` or `mda_check_no_verdict`) with the `session_id`, the `client_ip`, the sender and the recipients known at that point, and the reason.
2. Every admitted connection that sent at least one line writes one `session_closed` line, however it ended.
3. Every exception of ours writes one ERROR line with its traceback (`handler_error` inside a command, answered `421`; `session_error` outside one; `unhandled_error` anywhere else in the event loop).

The only refusals below INFO are the ones a peer can produce without limit, and they are listed at the end of this section with the counter that replaces the line. Code that adds a refusal goes through `handler._refuse` or simply sends its reply through `SMTPConnection._reply`, which logs any 4xx/5xx the handler did not claim; `tests/test_logfmt.py` fails on a log event that has not been classified.

The log is logfmt, one line per event, and every line about a connection carries the same `session_id`. Three kinds of line name addresses, at INFO:

| Event | One per | Says |
|---|---|---|
| `message_delivered` / `message_deferred` / `message_rejected` | message that reached the end of DATA | what the MDA made of it |
| `reject` | 4xx/5xx reply to a command (`verb=`, `reason=`, `code=`, `sender=`, and `recipient=` or `recipients=` where there is one) | why |
| `session_closed` | connection the gate admitted and that sent at least one line | everything else |

`reject` covers the refusals the protocol makes before the handler is asked too. Their `reason=`: `unknown_parameter` (555), `oversize_message` (552 for a body over the size limit), `oversize_announced` (552 for a `SIZE=` over it), `bad_size_parameter` (501), `malformed_address` (553, brackets or quotes that never close), `bad_sequence` (503), `bad_syntax` (501, or a 500 for a line with no command word), `unknown_command` and `line_too_long` (500), `not_implemented` (502), `too_many_commands` (421, a per-verb call limit), `too_many_unknown_commands` (502 ... goodbye), `shutdown` (421 to a command that would start new work during the drain), and `protocol` for any other code. They also carry the reply text in `reply=` and the command argument in `arg=`. A line refused before any verb ran (too long, unknown verb) has `verb=null` and the line itself in `arg=`. Each names the sender of the transaction it belongs to, which matters on a connection that carries several messages. A session gets at most `PYMTA_MAX_ERRORS_PER_SESSION` of these; `errors=` on `session_closed` still counts them all.

`session_closed` is Postfix's `disconnect from` line, and the one to search when a sender reports mail that never arrived and nothing else mentions it. Those are the sessions that ended without any refusal: a STARTTLS the sender's side gave up on, a timeout, a hang-up mid-DATA, a shutdown that cut the session.

```text
event=session_closed session_id=9bf92de8 client_ip=192.0.2.10 helo=mx.sender.example tls=true
  tls_cipher=TLS_AES_256_GCM_SHA384 tls_version=TLSv1.3 sender=alice@sender.example
  commands="ehlo=2 starttls=1 mail=1 rcpt=1 data=0/1" errors=1 closed_by=peer_closed duration_ms=30412
  last_error_verb=data last_error_reply="552 5.3.4 Message size exceeds fixed maximum" last_error_arg=null
```

| Field | Meaning |
|---|---|
| `commands` | Per verb, `n` when all succeeded, `succeeded/attempted` otherwise. A `data=0/1` is a message that did not get through |
| `last_error_*` | The last 4xx/5xx sent, whoever sent it, with the verb and the argument that drew it. An `AUTH` argument is logged as `[redacted]`: it can carry credentials |
| `sender` | The MAIL FROM of the last transaction opened, accepted or not. Clipped in the middle past 256 characters, so the domain stays searchable |
| `recipients`, `recipient_count` | Only when a transaction was still open at the end: the recipients accepted for a message that was never completed |
| `tls`, `tls_version`, `tls_cipher`, `tls_error` | `tls_error` is set when the STARTTLS handshake failed, with the cause (`SSLError: ... certificate unknown`, `no shared cipher`, ...). A sender enforcing TLS that rejects our certificate looks exactly like this |
| `closed_by` | `quit` (answered `221`), `peer_closed`, `peer_reset` (with `error_type`), `server_closed` (we answered 421, or 502 after too many unrecognised commands), `command_timeout`, `data_timeout` (a message not received in time, or too slowly while message slots are scarce), `session_timeout`, `reclaimed` (idle, and its slot given to a source under its share), `tls_failed`, `internal_error`, `shutdown` (answered 421 because the process is stopping, or still open at the `PYMTA_SHUTDOWN_TIMEOUT` deadline) |
| `wire_peer` | The balancer, when PROXY protocol is on; `client_ip` is then the PROXY source |

So, for "mail from `@sender.example` never arrives": search `sender.example` (it appears in `sender=` and `helo=`), or the sender's IP in `client_ip`, then pull every line of that `session_id`.

Two cases leave nothing at INFO, on purpose. A connection refused before the greeting is what a flood is made of: it is counted in `pymta_connections_total{result=...}` and logged at DEBUG with the client IP (`connection_draining`, `connection_blocked`, `connection_capped`, and `proxy_header_untrusted` / `proxy_header_invalid` with PROXY protocol on). The one exception is `connection_capped`: a sender held off by a session cap is deferred mail, so its first refusal reaches INFO with `client_ip=` and `scope=`, then at most one line a minute per address, with `suppressed=` counting the refusals in between. At most 1024 addresses are followed at a time and `PYMTA_LOG_SESSIONS=false` turns it off. And a connection that never sends a line, whether it closes at once (a TCP health check) or idles until the timeout, is logged at DEBUG. If neither the log nor that counter moves, the sender is not reaching this host: check their side, our MX records and anything in front of port 25.

`pymta_refusals_total{verb, code}` counts every 4xx/5xx sent, by the verb it answered (`none` for a line refused before any command ran) and the reply code: `sum by (code) (rate(pymta_refusals_total[5m]))` is the first graph to look at.

**Saved queries.** The lines above are meant to be counted. In LogQL, with `{app="mta-in-py"}` standing for your stream selector:

```logql
# Why commands are refused
sum by (reason) (count_over_time({app="mta-in-py"} | logfmt | event="reject" [1h]))

# One reason in detail: who sends what we refuse
{app="mta-in-py"} | logfmt | event="reject" | reason="unknown_parameter"
topk(20, sum by (sender) (count_over_time({app="mta-in-py"} | logfmt | event="reject" | verb="mail" [24h])))

# How sessions end
sum by (closed_by) (count_over_time({app="mta-in-py"} | logfmt | event="session_closed" [1h]))

# STARTTLS handshakes that failed, by cause (old TLS versions show up here)
sum by (tls_error) (count_over_time({app="mta-in-py"} | logfmt | event="session_closed" | tls_error!="" [24h]))

# TLS versions in use, and sessions in clear
sum by (tls, tls_version) (count_over_time({app="mta-in-py"} | logfmt | event="session_closed" [24h]))

# Senders held off by a session cap
sum by (client_ip, scope) (count_over_time({app="mta-in-py"} | logfmt | event="connection_capped" [1h]))

# Parameters accepted and ignored
sum by (ignored_parameters) (count_over_time({app="mta-in-py"} | logfmt | event="session_closed" | ignored_parameters!="" [24h]))

# Everything about one session
{app="mta-in-py"} | logfmt | session_id="9bf92de8"

# Faults of ours: should be empty
{app="mta-in-py"} | logfmt | event=~"handler_error|session_error|unhandled_error|fatal_error"
```

Without Loki the same reads work with `grep 'event=reject' | grep -o 'reason=[^ ]*' | sort | uniq -c`.

`pymta_commands_total{verb, class}` counts the same thing as `commands=`, as a metric: a rising `class="5xx"` on one verb, or `class="error"` on `STARTTLS`, is the aggregate view of these lines.

### Naming

Every variable pymta owns is namespaced: `PYMTA_*` for the SMTP server itself, `MDA_*` for the channel to the MDA. The only unprefixed names it reads are the four Postfix-image fallbacks listed below — `MAX_INCOMING_EMAIL_SIZE`, `MYHOSTNAME`, `ENABLE_PROXY_PROTOCOL` and `STARTTLS_CHAIN_FILES` — and each is read only when its prefixed counterpart is unset, so nothing else the Postfix image defines can change pymta's behaviour by accident. `MYORIGIN` and `MYDOMAIN` have no counterpart at all, because pymta never rewrites or completes an envelope address: it requires a fully-qualified one and rejects the rest.

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
| **Behind a load balancer** | `PYMTA_ENABLE_PROXY_PROTOCOL=true` | **required**: the balancer's IPs/CIDRs | the PROXY header |
| **Directly exposed** | off | ignored | the TCP peer |

**A load balancer without PROXY protocol is not supported.** pymta would see only the balancer's address, so every session would bucket under one IP, and the per-source share would silently become a much lower global cap, and every message would be stamped with the balancer's own IP as the sender's, in `Received` and in the envelope the MDA stores. There is no setting for this topology and no fallback that makes it work.

**With PROXY protocol on, the SMTP port must be reachable by the balancer only** (a network policy, a security group, a private interface). pymta reads the PROXY header before it checks who sent it and before the connection counts against any limit: up to 64 KiB per connection, for `PYMTA_PROXY_PROTOCOL_TIMEOUT` seconds. From the balancer that is a few dozen bytes it wrote itself. From anyone else it is memory outside every session cap, and `PYMTA_TRUSTED_PROXIES` only refuses such a peer once its header has been read.

pymta refuses to start with PROXY protocol on and `PYMTA_TRUSTED_PROXIES` empty. A deployment that cannot name its balancer at boot sets `0.0.0.0/0,::/0`: **every peer's header is then trusted**, startup logs a `proxy_trust_unrestricted` warning, and the network isolation is the *entire* trust boundary. Name the balancer whenever you can.

The one thing pymta does enforce: with PROXY protocol on it never falls back to the TCP peer for `client_address`, so a header-less connection (a v2 `LOCAL` health check) reports no client rather than naming the balancer.

Network isolation has to come from the deployment. A PROXY header is trusted on the word of the peer that sent it, and it sets both the key for every per-IP cap and the `client_address` the MDA writes into `Received`. A peer that can open a TCP connection to port 25 directly (a container port published on a node, a second interface, a foothold on an internal network) can otherwise spread a forged source across the address space until only the global cap applies, and attribute its mail to any IP it names. Enforce the isolation in the NetworkPolicy or firewall as well as in the allowlist.

**2. `MDA_API_BASE_URL` must be `https://`.** The MTA→MDA channel is authenticated by a bearer token; over plaintext to a non-local host, anyone on the path can read it. pymta logs a startup warning when it sees `http://` pointing anywhere but localhost.

**3. `MDA_API_SECRET` should be at least 32 bytes of real entropy, and not the dev value.** It is an HS256 shared secret: short ones are brute-forceable offline from a single captured JWT, and `my-shared-secret-mda` is in the repo. pymta warns at startup below 32 bytes, and refuses to start if the secret is missing entirely.

**4. Set `PYMTA_METRICS_API_KEY`, and keep the metrics port off the interface that serves port 25.** `PYMTA_METRICS_BIND_HOST` defaults to `0.0.0.0` because the usual scrape paths (Prometheus hitting the pod IP, a compose port mapping) cannot reach a loopback-only listener.

Set the key. Scrape with `Authorization: Bearer <key>`, the same arrangement as the backend's `PROMETHEUS_API_KEY` (see `core.middlewares.PrometheusAuthMiddleware`), so one scrape config covers both. Most exporters ship unauthenticated and lean on network policy, which is why it is opt-in here too, but two things argue for turning it on. The exposition carries no addresses and no message content, but it does carry volumes, rejection reasons, breaker state, and the configured limits themselves, which tells an attacker how much traffic it takes to hit each cap. And the exposition server runs inside the SMTP process, threading one connection at a time, so an open port 9100 is a thread-exhaustion path to port 25 and not only a disclosure. Handlers carry a 10 s socket timeout to bound that. The key and a NetworkPolicy are what close it.

Where you cannot authenticate, restrict with a NetworkPolicy, or set `PYMTA_METRICS_BIND_HOST=127.0.0.1` / `PYMTA_METRICS_BIND_PORT=0`.

**5. Size the session-slot budget.** `PYMTA_MAX_CONCURRENT_DATA` sets both the memory ceiling and, at 3x, the session slots; `PYMTA_SESSION_TIMEOUT` bounds how long one connection can occupy one.

Every other timeout is re-armed by peer activity, so a peer that stays marginally active can hold a session open for far longer than `PYMTA_COMMAND_TIMEOUT` suggests. The session cap is the one bound that is not re-armed. It does not prevent a distributed attacker from occupying slots, but it means blocking one frees the slots instead of leaving them held until the process restarts, and it stops `PYMTA_SHUTDOWN_TIMEOUT` (25 s) from severing long-lived sessions on every rollout.

**The per-source share is `PYMTA_MAX_SHARE_PER_SOURCE`** (0.1: 12 sessions and 4 messages in flight per source on the defaults). Mind it during a queue flush: after pymta downtime or a tripped MDA breaker, every sender with a backlog retries at full concurrency, and a sender that comes from one or two addresses is held to its share while it drains. Raise the fraction for the duration if that is what `pymta_connections_total{result="rejected_per_ip"}` shows.

Slot exhaustion on an *inbound* MTA delays mail rather than losing it: once the global cap is hit, new connections get `421` and close, and SMTP senders retry for days. Watch `pymta_sessions_active` and the upper buckets of `pymta_session_duration_seconds`; a healthy inbound MX has sessions measured in seconds.

Also review `PYMTA_DATA_TIMEOUT` against your real size cap; it sets a floor on how slow a legitimate sender may be. At the 10 MiB default, 300 s means a sender has to sustain roughly 35 kB/s.

## Address normalisation is a cross-service contract

pymta lower-cases the domain and preserves the local-part's case; the MDA matches mailboxes on the `(local_part, domain)` tuple. RCPT-check and deliver send the same string, so the two always agree with each other. The MDA lower-cases the ASCII letters of the local-part before it looks the mailbox up, in the check and in the delivery alike. Recorded here so the two services do not drift apart.

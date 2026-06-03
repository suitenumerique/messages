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
| Prometheus metrics | — | `/metrics` on port `9100` |
| Tests | `make test-mta-in` | `make test-mta-in-py` |
| Lint | `make lint-mta-in` | `make lint-mta-in-py` |

Both run as a stateless, queue-less SMTP front-end. After receiving an email through SMTP each message is processed synchronously during the SMTP session by:

- Validating each recipient with a REST API call to `{env.MDA_API_BASE_URL}/inbound/mta/check/` during the RCPT TO command.
- Delivering the complete message via REST API call to `{env.MDA_API_BASE_URL}/inbound/mta/deliver/` during the DATA command.
- Translating the MDA outcome (200 + `status=ok` / 5xx / timeout) into a single SMTP reply line.

The API calls are secured by an HS256 JWT token using a shared secret `env.MDA_API_SECRET` whose `body_hash` claim binds it to the exact bytes being posted.

## When to use which

Postfix is the production default. The pymta implementation is offered side-by-side so it can take over once parity is proven; it is easier to extend (no milter protocol, no C glue), gives us Prometheus metrics, and reduces the attack surface (no Postfix binary, no `libmilter`, no on-disk queue at all).

Switching production from one to the other only requires re-pointing the inbound public IP to the other container — the MDA back-end and the env vars are identical. PROXY-protocol passthrough is toggled by the same `ENABLE_PROXY_PROTOCOL=haproxy` env var on both.

## Running

```
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
| `PYMTA_HOSTNAME` | `mta-in` | Banner / Received-header host name |
| `PYMTA_SMTP_HOST` / `PYMTA_SMTP_PORT` | `0.0.0.0` / `25` | SMTP listener bind |
| `PYMTA_METRICS_HOST` / `PYMTA_METRICS_PORT` | `0.0.0.0` / `9100` | Prometheus endpoint (set port to 0 to disable) |
| `PYMTA_MAX_RECIPIENTS` | `100` | RCPT TO cap per envelope |
| `PYMTA_MAX_ENVELOPES_PER_CONNECTION` | `10` | Envelopes per TCP session |
| `PYMTA_HARD_ERROR_LIMIT` | `50` | 4xx/5xx replies before forcing 421 + disconnect |
| `PYMTA_MAX_SESSIONS_PER_IP` | `100` (0 = off) | Per-IP concurrent session cap |
| `PYMTA_MAX_SESSIONS_TOTAL` | `1000` (0 = off) | Process-wide concurrent session cap |
| `PYMTA_COMMAND_TIMEOUT` | `120` | Per-command idle timeout (s) |
| `PYMTA_DATA_TIMEOUT` | `600` | Total DATA-phase deadline (s) |
| `PYMTA_TLS_CERT_FILE` / `PYMTA_TLS_KEY_FILE` | empty | STARTTLS chain (empty = STARTTLS off) |
| `PYMTA_ENABLE_SMTPUTF8` | `true` | Advertise SMTPUTF8 in EHLO |
| `ENABLE_PROXY_PROTOCOL` | unset | Set to `haproxy` to enable PROXY-protocol v1/v2 |

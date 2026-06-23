# realtime-relay

A tiny ASGI service that holds the browser's long-lived **SSE** connections and
relays per-user / per-room realtime events to them, fanned out from a **Redis**
bus. The Django backend (WSGI) stays connection-free: it just `PUBLISH`es to
Redis after a delivery commits.

```
Django/worker ──PUBLISH rtchannel:user:<id>──▶ Redis ──psubscribe──▶ relay ──SSE──▶ browser
```

## Why a separate process
SSE means one held connection per client — a poor fit for gunicorn's sync
workers. The relay is a small async (Starlette/uvicorn) app that holds those
connections on its own event loop. It has no DB and no Django import; the only
thing it shares with the backend is the Redis bus.

## The contract (shared with the backend)

**Connection token.** The browser first asks the authenticated Django backend
for a short-lived JWT, then opens `EventSource('/realtime-relay/?token=<jwt>')`
(EventSource can't send `Authorization` headers, hence the query param). The
relay only ever *verifies* the token — signed HS256 with `REALTIME_JWT_SECRET`,
shared with the backend. Claims:

| claim | meaning |
|-------|---------|
| `sub` | user id (always joins `user:<sub>`) |
| `rooms` | extra channels this connection may join, e.g. `["thread:<id>"]` — the backend computed the ACL; the relay trusts it |
| `exp` | required; short-lived (the backend hardcodes ~60s), the client re-mints on reconnect |

This keeps room authorization in Django where the data lives. Presence
(`thread:<id>` rooms) is purely additive: the backend just lists more rooms.

**Bus.** The backend publishes to `rtchannel:<room>` (e.g. `rtchannel:user:<id>`,
`rtchannel:thread:<id>`). The `rtchannel:` prefix is hardcoded on both sides
(not configurable) and MUST match. Payload is JSON:
`{"event": "inbox.changed", "data": {...}, "id": "<optional>"}`. Keep `data`
thin — a "what changed" hint, not message content; the client refetches over
the authenticated API (same privacy stance as the webhook `jmap_metadata`).

## Endpoints
- `GET /realtime-relay/?token=…` — the SSE stream. Emits a `ready` event, then events, plus heartbeat comments.
- `GET /__lbheartbeat__` — liveness (always 200).
- `GET /healthz` — readiness (pings Redis).

## Config (env)
| var | default | notes |
|-----|---------|-------|
| `REDIS_URL` | `redis://redis:6379` | **must** be the same instance the Django backend publishes to (it reads the same `REDIS_URL`); the Celery broker instance is fine |
| `REALTIME_JWT_SECRET` | — | **must** match the backend; relay fails closed if unset |
| `REALTIME_JWT_ALGORITHM` | `HS256` | |
| `REALTIME_HEARTBEAT` | `15` | keep well under Scalingo's idle window (~30s) — it cuts a stream that sends no bytes for that long |
| `REALTIME_MAX_CONNECTIONS` | `0` | per-process cap; 0 = unlimited |
| `REALTIME_CORS_ORIGINS` | — | comma-separated allowed origins for the SSE endpoint. Empty = same-origin only (prod, behind Caddy). Set to the frontend origin in dev, where the relay is a separate origin/port. |

## Deploy — two targets
Self-contained component: its deps live in `src/realtime/pyproject.toml`, never
in the backend's. It runs one of two ways depending on the target:

- **Docker** — its own image, built from `src/realtime` (`runtime-prod`, a
  distroless non-root image) and published by `messages-ghcr.yml`, deployed as
  its own app, like `mta-out` / `socks-proxy`. The image has no shell, so
  `python -m realtime_relay` (not a CMD `sh -c`) reads `$PORT` and launches
  uvicorn.
- **Scalingo** (buildpacks, single app) — co-hosted in the backend web dyno:
  `bin/scalingo_postcompile` runs `pip install ./src/realtime` into the web venv
  and `bin/scalingo_run_web` launches `uvicorn realtime_relay.app:app` next to
  gunicorn. Same code + deps; it just shares the dyno (so a relay crash is
  isolated by the restart loop there, but not the process/venv).

Either way Caddy reverse-proxies `/realtime-relay/*` to the relay via
`MESSAGES_FRONTEND_REALTIME_SERVER` (`{ flush_interval -1 }` so the stream isn't
buffered). WebSockets/SSE need no Scalingo config — the only requirement is the
heartbeat above. HTTP/2 to the browser is provided by Caddy; uvicorn speaks
HTTP/1.1 upstream.

## Dev
Fully self-contained in `src/realtime/` (own `pyproject.toml`, `uv.lock`,
`Dockerfile`, tests). In dev it runs as its **own container on its own port** —
the `realtime-relay` compose service, reachable at **`http://localhost:8905`**
(`/realtime-relay/` for the stream, `/healthz`). The browser talks to it
cross-origin in dev, so it's CORS-enabled for `http://localhost:8900`.

```bash
make test-realtime          # tests (own image, zero infra — fakeredis)
make lint-realtime          # ruff format + check
make deps-lock-realtime     # re-lock src/realtime/uv.lock after a dep change

# smoke the running dev relay:
curl http://localhost:8905/healthz
```

**To actually exercise realtime in dev** (it ships dark — `REALTIME_ENABLED`
defaults False), set on the **backend** (`env.d/development/backend.local`):

```
REALTIME_ENABLED=true
REALTIME_JWT_SECRET=dev-realtime-secret          # MUST match the relay container
```

The frontend reads `REALTIME_ENABLED` from `/config`, mints a token at
`POST /api/v1.0/realtime/token/`, then opens its SSE stream at
`<NEXT_PUBLIC_REALTIME_ORIGIN>/realtime-relay/`. The path is hardcoded; only the
origin varies: in dev `NEXT_PUBLIC_REALTIME_ORIGIN=http://localhost:8905` points
straight at this container (cross-origin, hence the CORS allow-list above); in
prod it's empty, so the stream is same-origin and Caddy proxies
`/realtime-relay/*` here.

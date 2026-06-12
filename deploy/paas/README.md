# Messages — PaaS deployment

Buildpack scripts for deploying [Messages](https://github.com/suitenumerique/messages) on a PaaS (Scalingo, Clever Cloud, …).

## Layout

```
deploy/paas/
├── buildpack_postcompile.sh   # Build-time: slim the slug (drop sources, dev files)
├── buildpack_postfrontend.sh  # Build-time: assemble frontend/backend + download Caddy
└── buildpack_start.sh         # Runtime: web process entry point
```

The root `Procfile` references `deploy/paas/buildpack_start.sh` for the `web` process. Workers (`workerall`, `workerimports`, `workerreindex`, `workerrest`) are unchanged.

## Reverse proxy

Messages uses **Caddy** as its reverse proxy (downloaded inside `buildpack_postfrontend.sh`). The runtime `Caddyfile` is copied from `src/frontend/caddy/Caddyfile` — `{$ENV}` substitution is supported natively.

## Scalingo wiring

Build-time hooks are invoked via the [La Suite buildpack](https://github.com/suitenumerique/buildpack):

```bash
scalingo env-set BUILDPACK_URL="https://github.com/suitenumerique/buildpack#main"
scalingo env-set LASUITE_SCRIPT_POSTCOMPILE="deploy/paas/buildpack_postcompile.sh"
scalingo env-set LASUITE_SCRIPT_POSTFRONTEND="deploy/paas/buildpack_postfrontend.sh"
```

> **Operators upgrading from a previous deployment** must update `LASUITE_SCRIPT_POSTCOMPILE` and `LASUITE_SCRIPT_POSTFRONTEND` — the scripts moved from `bin/scalingo_*` to `deploy/paas/buildpack_*.sh`. See the [CHANGELOG](../../CHANGELOG.md) entry under `[Unreleased]` for the exact commands.

## Other deployments

* Docker Compose: see root `compose.yaml`.

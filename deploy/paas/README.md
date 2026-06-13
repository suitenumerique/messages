# Messages — PaaS deployment

Buildpack scripts for deploying [Messages](https://github.com/suitenumerique/messages) on a PaaS (Scalingo, Clever Cloud, …).

## Scope: PaaS covers only the web app and workers

The buildpack + `Procfile` deploy only:

- `web`: Django backend + Caddy reverse proxy serving the Next.js frontend
- `workerall`, `workerimports`, `workerreindex`, `workerrest`: background workers
- `postdeploy`: `python manage.py migrate`

**Every other Messages component must be deployed elsewhere and reached over the network:**

| Component | Why it can't live on the PaaS |
|---|---|
| `mta-in` (SMTP, port **25** inbound) | Most PaaS providers do **not** allow binding privileged ports or expose port 25. **Hard blocker on Scalingo.** |
| `mta-out` (SMTP, port 587 outbound) | Outbound SMTP is often blocked from PaaS providers, and email reputation requires a stable, dedicated egress IP — which the PaaS cannot guarantee since instances may be rescheduled on different hosts (with different IPs) on each redeploy. |
| `mpa` (rspamd) | No hard PaaS blocker, but must scan mail synchronously for `mta-in`, so it gets co-located with it. |
| `socks-proxy` | No hard PaaS blocker, but provides the stable egress IP for `mta-out`. That IP must also be **not blacklisted** by major anti-spam reputation lists (Spamhaus, Barracuda, SORBS, …) — PaaS shared IP pools offer no such guarantee, so `socks-proxy` runs on a dedicated host where the IP can be vetted, monitored and kept clean. |
| OIDC provider | Provision a managed Keycloak elsewhere or use an external IdP. |

**Managed addons** — on Scalingo, PostgreSQL, Redis, [OpenSearch](https://scalingo.com/databases/opensearch) and S3-compatible Object Storage are all available as managed services. Wire them to the `web`/worker processes via `DATABASE_URL`, `REDIS_URL`, the OpenSearch connection env vars and the AWS S3 variables. Other PaaS providers vary — check your provider's catalog.

In practice, a PaaS deployment of Messages is **hybrid**: the web/worker tier on the PaaS, the mail tier (mta-in/mta-out/mpa/socks-proxy) on a VPS or Ansible-managed hosts. See [`docs/self-hosting.md`](../../docs/self-hosting.md) and [`suitenumerique/st-ansible`](https://github.com/suitenumerique/st-ansible) for the mail-tier side.

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

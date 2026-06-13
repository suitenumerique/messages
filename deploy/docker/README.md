# Messages — production deployment

The recommended way to deploy Messages in production is the official Ansible collection:

→ **[`suitenumerique/st-ansible`](https://github.com/suitenumerique/st-ansible)** — deploys the full Messages stack as rootless Podman containers managed by systemd user units.

- Role: [`roles/messages`](https://github.com/suitenumerique/st-ansible/tree/main/roles/messages)
- Per-component docs: [`docs/02-messages`](https://github.com/suitenumerique/st-ansible/tree/main/docs/02-messages) — covers app, workers, mta-in, socks-proxy, mpa

If you need to roll your own Docker Compose stack instead, the Jinja2 templates under [`roles/messages/templates/`](https://github.com/suitenumerique/st-ansible/tree/main/roles/messages/templates) are the closest reference — in particular [`compose.yaml.j2`](https://github.com/suitenumerique/st-ansible/blob/main/roles/messages/templates/messages/compose.yaml.j2) for the main app, plus similar templates under `mpa/`, `mta_in/`, `socks_proxy/` and `workers/`. They describe exactly the service split, env wiring, and dependency graph the team runs in production. Podman quadlets and Docker Compose share the same OCI image model and similar service primitives, so the mapping back to Compose is usually straightforward.

## Development

For local development, the root [`compose.yaml`](../../compose.yaml) is the supported entry point — see the [README](../../README.md) and [`docs/self-hosting.md`](../../docs/self-hosting.md).

## Other deployments

* PaaS (Scalingo, Clever Cloud, …): see [`deploy/paas/`](../paas/).
* Kubernetes: no Helm chart published yet — see [`docs/self-hosting.md`](../../docs/self-hosting.md#kubernetes-deployment) for the current status.

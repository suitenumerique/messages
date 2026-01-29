# Background Task Worker

The application uses a background task worker to process asynchronous jobs like email processing, file imports, and search indexing.

## Quick Start

```bash
# Start with all queues and scheduler (default)
python worker.py

# Start with specific queues only
python worker.py --queues=inbound,outbound

# Exclude low-priority queues
python worker.py --exclude=reindex,imports

# Disable the scheduler (for secondary workers)
python worker.py --disable-scheduler
```

## Queues

Tasks are routed to specific queues based on their type. Queues are listed in priority order.

| Priority | Queue | Description |
|----------|-------|-------------|
| 1 (highest) | `management` | Admin/management tasks (migrations, cleanup) |
| 2 | `inbound` | Inbound email processing (time-sensitive) |
| 3 | `outbound` | Outbound email sending and retries |
| 4 | `default` | General tasks (fallback for unrouted tasks) |
| 5 | `imports` | File import processing (MBOX, EML, IMAP) |
| 6 (lowest) | `reindex` | Search indexing |

### Queue Routing

Tasks are automatically routed to queues based on their module:

| Task Module | Queue |
|-------------|-------|
| `core.mda.inbound_tasks.*` | `inbound` |
| `core.mda.outbound_tasks.*` | `outbound` |
| `core.services.importer.tasks.*` | `imports` |
| `core.services.search.tasks.*` | `reindex` |
| Everything else | `default` |

## CLI Options

| Option | Description |
|--------|-------------|
| `--queues`, `-Q` | Comma-separated list of queues to process |
| `--exclude`, `-X` | Comma-separated list of queues to exclude |
| `--concurrency`, `-c` | Number of worker processes (default: CPU count) |
| `--disable-scheduler` | Disable the task scheduler (enabled by default) |
| `--loglevel`, `-l` | Logging level (default: INFO) |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `WORKER_CONCURRENCY` | Default concurrency if `--concurrency` not specified |
| `CELERY_CONCURRENCY` | Fallback for `WORKER_CONCURRENCY` (legacy) |

## Deployment

### Scalingo (Procfile)

```text
worker: python worker.py
```

### Docker Compose

The `worker-dev` service in `compose.yaml` runs the worker for local development:

```yaml
worker-dev:
  command: ["python", "worker.py", "--loglevel=DEBUG"]
```

### Running Multiple Workers

For high-throughput deployments or strict queue isolation, run specialized workers for different queue groups:

```bash
# Worker 1: High-priority email processing (with scheduler)
python worker.py --queues=management,inbound,outbound

# Worker 2: Background tasks only (no scheduler)
python worker.py --queues=default,imports,reindex --disable-scheduler
```

This ensures that low-priority tasks (imports, reindex) never compete with email processing.

## Monitoring

The `worker-ui` service provides a web UI for monitoring tasks (Flower). Access it at `http://localhost:8903` in development.

Task events are enabled by default for monitoring tools.

## Scheduled Tasks

The scheduler is enabled by default. The following tasks are scheduled:

| Task | Schedule | Queue |
|------|----------|-------|
| Retry pending messages | Every 5 minutes | `outbound` |
| System selfcheck | Configurable interval | `outbound` |
| Process inbound queue | Every 5 minutes | `inbound` |

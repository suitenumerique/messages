# Background Task Worker

The application uses a background task worker to process asynchronous jobs like email processing, file imports, and search indexing.

It is built on [Dramatiq](https://dramatiq.io/) over the
[`dramatiq-redis-streams`](https://github.com/sylvinus/dramatiq-redis-streams) broker, which
consumes with `XREADGROUP BLOCK` (event-driven, no polling) and recovers work from dead workers
on a per-task deadline. Application code never imports Dramatiq directly — it goes through
`core/task_utils.py`, described in [Writing tasks](#writing-tasks) below.

## Quick Start

```bash
# Start with all queues and the scheduler (default)
python worker.py

# Start with specific queues only
python worker.py --queues=inbound,outbound

# Exclude low-priority queues
python worker.py --exclude=reindex,imports

# Disable the scheduler (for secondary workers)
python worker.py --disable-scheduler
```

## Queues

| Priority | Queue | What runs there | Consumed by |
|---|---|---|---|
| 0 (highest) | `inbound` | Inbound mail processing + its 5-minute requeue sweep | `workerrest` |
| 10 | `outbound` | Sending, selfcheck, the retry sweep | `workerrest` |
| 20 | `default` | Webhooks, push, calendar, exports, purges, history prune | `workerrest` |
| 30 | `imports` | Import runs — long, sequential, resumable | `workerimports` (alone) |
| 40 | `blobs` | Hourly blob GC and offload to object storage | `workerblobs` (alone) |
| 50 (lowest) | `reindex` | Search indexing | `workerreindex` (alone) |

Routing lives on the task — `@register_task(queue="inbound")` — not in a settings-level table of
module globs. To see where a task runs, read its decorator. An unknown queue name raises at import
rather than silently declaring work no worker consumes.

### What priority does, and doesn't, do

Dramatiq prioritises **per actor, not per queue**. A worker runs one consumer thread per queue it
consumes, and they all feed a single shared priority queue that worker threads take the lowest
number from first (ties broken FIFO). So passing queues to `--queues` in a particular order
achieves nothing by itself.

The numbers above are therefore stamped onto each task, from `QUEUE_PRIORITIES` in
`core/task_utils.py`. That is what makes an inbound message beat a reindex when both are sitting in
the same worker. Override it per task with `@register_task(queue=..., priority=N)`.

Two limits worth being clear about:

- **Priority only orders work a process has already reserved.** It cannot stop a queue being
  starved by a busy neighbour — only a dedicated worker does that, which is why `imports`, `blobs`
  and `reindex` each get their own process.
- **It is not preemption.** A task already running keeps its thread until it finishes; a
  higher-priority message waits for the next free one.

### Choosing a queue for a new task

Two independent questions, in this order:

1. **How long does it run, and how often?** Anything that is *chronically* long — minutes at a
   time, on a schedule — needs a queue of its own (`LONG_RUNNING_QUEUES`), because it strands
   whatever is reserved beside it (see [Delivery semantics](#delivery-semantics)). Something that
   is *occasionally* long, like an operator-triggered export, can stay on a shared queue; add it to
   `SHARED_LONG_TASKS` so the guard test stays honest.
2. **What must not be slowed down by it?** Tasks doing external network I/O against third parties —
   webhooks, push gateways, CalDAV — stay off `inbound` however time-sensitive they feel. A slow
   customer endpoint or a degraded APNs must never consume the workers that deliver mail. This is
   why *non-blocking* webhooks are dispatched to `default` while *blocking* ones (which can hold or
   drop a message) run inline in the inbound task instead.

Volume matters too: push fans out one task per device per recipient, so it outnumbers inbound
messages several times over — another reason it does not belong on the queue whose latency matters
most.

## CLI Options

| Option | Description |
|--------|-------------|
| `--queues`, `-Q` | Comma-separated list of queues to process |
| `--exclude`, `-X` | Comma-separated list of queues to exclude |
| `--concurrency`, `-c` | Number of worker processes (default: CPU count) |
| `--threads` | Threads per worker process (default: 1) |
| `--disable-scheduler` | Disable the periodic task scheduler (enabled by default) |
| `--loglevel`, `-l` | Logging level (default: INFO; `DEBUG` turns on verbose worker logs) |

`--concurrency` sets **processes** and `--threads` defaults to 1, so a worker runs exactly
`--concurrency` tasks at a time. Raise `--threads` (or `WORKER_THREADS`) for I/O-bound queues where
tasks spend their time waiting on the network.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `WORKER_CONCURRENCY` | Default concurrency if `--concurrency` is not specified |
| `WORKER_THREADS` | Default threads per process if `--threads` is not specified |
| `TASK_BROKER_URL` | Redis URL for the broker and result backend |
| `TASK_BROKER_NAMESPACE` | Key prefix for all broker keys (default `dramatiq`) |
| `TASK_RESULT_TTL` | Seconds a task result stays readable (default 86400) |
| `TASK_HISTORY_ENABLED` | Persist every task to Postgres for the admin task history (default off) |
| `DISABLE_TASK_SCHEDULE` | Register no periodic schedules at all |
| `dramatiq_queue_prefetch` | Messages reserved per consumer thread (defaults to 1 — see [Delivery semantics](#delivery-semantics)) |

### Redis requirements

The broker needs **Redis >= 7.0**, and the instance it points at must run with
`maxmemory-policy noeviction` and AOF persistence. A queue is not a cache: under an eviction
policy Redis will silently drop stream and pending-list keys under memory pressure, and enqueued
tasks vanish with no error. If your Redis is shared with an eviction-enabled cache, point
`TASK_BROKER_URL` at a different instance or database.

## Writing tasks

```python
from core.task_utils import cron_task, register_task, set_task_progress

@register_task(queue="outbound")
def send_something_task(thing_id):
    set_task_progress(50, {"message": "Halfway"})
    return {"success": True}
```

Dispatch with `send_something_task.delay(thing_id)`, which returns a task whose `.id` can be
handed to the client and polled through `GET /api/v1.0/tasks/<task_id>/`. Call
`task.track_owner(request.user.id)` first — the endpoint refuses to report on a task the caller
does not own.

All durations passed to `register_task` are in **seconds**.

### Retries

Retries are opt-in. A task that raises is dead-lettered immediately unless it asks otherwise:

```python
@register_task(
    queue="reindex",
    max_retries=5,
    retry_on=(TransientError,),   # anything else dead-letters at once
    max_backoff=600,              # exponential + jittered, capped here
)
def flaky_task(...):
    ...
```

### Delivery semantics

Delivery is **at-least-once**. This is a change from the Celery setup, which acked a message on
receipt and therefore *lost* the task if the worker died mid-run. Now the message is acked only
after the task returns, so a crash re-runs it — durability in exchange for the possibility of a
duplicate run. A task is re-run when:

- a worker dies (or is killed mid-deploy) while holding it;
- it outruns its `time_limit`, past which the broker considers it abandoned;
- a Redis failover loses the un-replicated ack.

**Any task with side effects must therefore be safe to run twice.** The ones that aren't naturally
idempotent guard themselves: `send_message` skips recipients already marked delivered and holds a
per-message lock, inbound processing dedups on `Message-ID` and caches blocking-webhook results,
imports resume from a watermark under a run lock, and push notifications collapse on-device. The
one window that remains open is a worker dying *between* an SMTP `DATA` being accepted and the
recipient row being marked delivered — that recipient gets the mail twice. Losing it silently, as
before, is the worse failure.

Two things keep duplicate runs rare rather than routine, and both are about a message's recovery
deadline starting when the broker **delivers** it, not when a worker starts running it:

- Workers reserve exactly one message per consumer thread (`dramatiq_queue_prefetch=1`, set in
  `worker.py`) rather than the library default of two, so nothing sits reserved behind the message
  actually being worked on. One extra round-trip per message buys that away.
- Chronically long tasks live on queues a worker consumes **alone**
  (`worker.LONG_RUNNING_QUEUES`: `blobs`, `imports`, `reindex`). A worker consuming several queues
  runs one consumer thread per queue, so the hourly 55-minute blob sweeps sharing a worker with
  sub-second webhooks would leave one message per sibling queue ageing past its deadline —
  reclaimed elsewhere, then run again locally. Since they run for most of every hour, that would be
  the normal state of affairs, which is why they get `blobs`.

Three tasks are knowingly long *and* on a shared queue — `retry_messages_task`,
`purge_abandoned_inbound_messages_task`, `export_mailbox_task` (`worker.SHARED_LONG_TASKS`). They
are an operator action and two normally-quick sweeps, so the cost is an occasional duplicate run
that the neighbour's own idempotency absorbs, rather than a permanent condition. `test_worker.py`
fails on any *new* long task outside that set, so adding one stays a deliberate decision.

`workerall` (every queue in one process) trades this away for simplicity and is meant for
development and small single-dyno installs; the split Procfile entries are the production shape.

### Time limits

Every task has a time limit — 10 minutes unless it declares otherwise — at which
`TaskTimeLimitExceeded` is raised inside it. Long-running tasks must raise their own ceiling:

```python
@register_task(queue="imports", time_limit=6 * 3600)
def run_import_task(channel_id):
    ...
```

This matters twice over: the limit also sets when the broker considers the task abandoned and
lets another worker reclaim it, so a task that routinely outlives its limit will be run twice.

### Scheduled tasks

Stack `@cron_task` above `@register_task`:

```python
@cron_task(crontab="*/5 * * * *")     # wall-clock schedule
@register_task(queue="outbound")
def retry_messages_task(...):
    ...

@cron_task(interval=settings.SEARCH_REINDEX_TASKS_INTERVAL)  # seconds, for setting-driven periods
@register_task(queue="reindex")
def process_pending_reindex_task():
    ...
```

The scheduler runs inside `python worker.py` (as a supervised `manage.py crontab` child process)
and takes a Redis lock, so leaving it enabled on every worker is safe — exactly one is ever live
and the rest block waiting on the lock, taking over within about a second if the leader dies. The
`--disable-scheduler` flag is there for workers that should never even try.

Two properties worth knowing. A job whose fire time slips is *dropped*, not run late, so the
misfire grace is widened to 5 minutes (`_configure_scheduler`) — the library default of one second
would silently skip a tick on any brief stall. And the lock has a 10-second TTL refreshed every 5,
so a leader frozen for longer than that can briefly overlap with its replacement; the idempotency
requirement above covers it.

Current schedule:

| Task | Schedule | Queue |
|------|----------|-------|
| `retry_messages_task` | Every 5 minutes | `outbound` |
| `selfcheck_task` | `MESSAGES_SELFCHECK_INTERVAL` | `outbound` |
| `process_inbound_messages_queue_task` | Every 5 minutes | `inbound` |
| `purge_abandoned_inbound_messages_task` | Daily, 03:17 | `default` |
| `process_pending_reindex_task` | `SEARCH_REINDEX_TASKS_INTERVAL` | `reindex` |
| `offload_blobs_task` | Hourly, :05 | `blobs` |
| `gc_orphan_blobs_task` | Hourly, :35 | `blobs` |
| `schedule_imports_task` | Every 5 minutes | `default` |
| `prune_task_history_task` | Daily, 02:45 | `default` |

To run any task synchronously, on demand:

```bash
python manage.py run_task core.services.blob_gc.gc_orphan_blobs_task --kwargs '{"mode": "full"}'
```

## Monitoring

The queue dashboard is served from the Django admin at `/admin/tasks/` (staff only) — in
development, <http://localhost:8901/admin/tasks/>. It shows per-queue backlog and throughput,
what each worker is currently holding, delayed messages, and the dead-letter queues, with
requeue and purge actions. It replaces the separate Flower service the Celery setup ran.

Set `TASK_HISTORY_ENABLED=True` to additionally record every task in Postgres, browsable under
*Django Dramatiq → Tasks* in the admin. It is off by default: it costs a synchronous `INSERT` on
enqueue, i.e. on the request path of every send and on every inbound delivery.

Set `TASK_PROMETHEUS_ENABLED=True` to have each worker expose Dramatiq metrics on port 9191.

## Deployment

### Scalingo (Procfile)

```text
workerall: python worker.py                                   # dev / small installs
workerimports: python worker.py --queues=imports --concurrency=1 --disable-scheduler
workerreindex: python worker.py --queues=reindex --concurrency=2 --disable-scheduler
workerblobs:   python worker.py --queues=blobs   --concurrency=1 --disable-scheduler
workerrest:    python worker.py --exclude=imports,reindex,blobs --concurrency=4
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
# Worker 1: time-sensitive email processing (with scheduler)
python worker.py --queues=inbound,outbound

# Worker 2: everything slow, each on a queue consumed alone
python worker.py --queues=imports --concurrency=1 --disable-scheduler
python worker.py --queues=blobs   --concurrency=1 --disable-scheduler
```

This keeps low-priority work (imports, reindex, sweeps) from competing with email processing — and,
per [Delivery semantics](#delivery-semantics), keeps a long task from stranding a short one
reserved beside it.

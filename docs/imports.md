# Resumable message import

Users import existing mail into a mailbox from an **EML**, **MBOX** or **PST**
file, or directly from an **IMAP** account. Imports can be large and long, so
they are designed to survive worker restarts, deploys and crashes and to resume
where they left off — without ever creating duplicate messages.

Gated behind `FEATURE_IMPORT_MESSAGES` (grants the `CAN_IMPORT_MESSAGES`
mailbox ability). Tuning env vars live in [env.md](env.md#message-import).

## Model: an import is a channel

Every import is a `Channel` with `type=import`, scoped to the destination
mailbox (`scope_level=mailbox`). Modelling it as a durable row (instead of
tracking Celery task state) means the run is listable, resumable and cancellable
by id long after any worker has forgotten it.

Durable state on the channel:

- `settings["import"]` — the run config and terminal snapshot: `source_type`
  (`eml`/`mbox`/`pst`/`imap`), `mode` (`oneshot`/`continuous`), `file_key`,
  final `status`/`success`/`failure`/`total`. Deliberately **lightweight**: the
  per-folder IMAP watermark is Redis-only (folder names are remote-controlled,
  so its size must never bloat the Postgres row — an eviction just costs a
  re-scan). The continuous poll cadence is **not** stored here either — it is
  the global `MESSAGES_IMPORT_IMAP_POLL_INTERVAL` setting.
- `encrypted_settings["imap"]` — IMAP credentials (server, port, username,
  password, SSL). Stored encrypted; kept for the life of the channel so a run
  can resume or (once continuous) keep polling. **Credentials are intentionally
  not scrubbed on completion** — a finished oneshot can be re-armed as
  continuous later without re-entering them. Deleting the channel frees them.
- `is_active` — `True` while the run should still make progress. Flipped to
  `False` when a oneshot finishes (or any import fails/is cancelled). A
  `continuous` IMAP channel stays `is_active=True` between polls.
- `last_used_at` — durable heartbeat, advanced as the run flushes progress.
  Drives the scheduler (below).

Live progress lives in **Redis** (`read_state`/`write_state`), keyed by channel
id: `status`, running `success`/`failure`/`total`, and the resume watermark
(`cursor` for files, `folders` for IMAP). The serializer reads `merged_state()`
= durable snapshot overlaid with the live Redis values, so the resource still
reads correctly after the cache is evicted (it simply falls back to the durable
terminal snapshot, or resumes from the start if a run was mid-flight).

## The run: one idempotent, resumable task

`run_import_task(channel_id)` runs (or resumes) a single import to completion.
It is idempotent and safe to re-dispatch:

1. Skip if the channel is gone or `is_active=False` (a finished oneshot must
   never be re-run by a stray scheduler tick).
2. Acquire a **run-lock** (Redis, TTL = `MESSAGES_IMPORT_STALL_TIMEOUT`) so a
   poll and a crash-recovery can't run concurrently. The lock is renewed before
   every message **and periodically through the initial file scan** (so a
   live-but-slow run never lets it lapse), and released in `finally`.
3. Advance the heartbeat, then dispatch to the per-source runner
   (`run_mbox` / `run_eml` / `run_pst` / `run_imap`), handing it the Redis
   watermark so it starts from where the last run stopped.
4. `mark_finished` writes the terminal status to both Redis and the durable
   snapshot and (for oneshot) sets `is_active=False`.

The runner beats the durable heartbeat and renews the lock before every message
(and through the pre-scan); it flushes counts + watermark to Redis every
~`FLUSH_EVERY` messages.

### Deduplication

Delivery goes through the normal inbound path with `is_import=True`. A re-run
(or a message present in two overlapping sources) is de-duplicated by
`Message-ID` when present, else by the SHA-256 of the raw bytes (`blob.sha256`),
both scoped to the destination mailbox. So resuming is always safe even if the
watermark rewinds.

### Storage tier

Imported bodies are written **directly to the object-storage tier**
(`create_blob(prefer_offloaded=True)`) instead of parking in the Postgres hot
tier until the periodic offload — a multi-gigabyte archive never bloats the
blobs table. The regular offload policy (master switch, size floor) still
applies, and without configured object storage (or on an upload failure) the
blob falls back to Postgres, exactly the pre-existing behavior.
See [tiered-storage.md](tiered-storage.md).

### File resume vs. IMAP resume

The two resume paths are deliberately asymmetric:

- **File imports (EML/MBOX/PST) resume by re-scanning from a positional
  cursor.** A resume re-indexes the mbox / re-walks the PST from the start and
  skips messages before `cursor`. This re-scan is **not optimized on purpose**:
  a file import only ever restarts on a deploy or a worker kill (a bounded, rare
  event), the ordering is deterministic so the rebuilt plan is identical, and
  dedup makes any overlap harmless. Simplicity wins over saving a one-off scan.
- **IMAP imports resume incrementally.** IMAP is the source that may run
  *continuously* (polling forever), so its resume must be cheap. State is a
  per-folder watermark `{folder: {uidvalidity, last_uid}}`; each run only
  `UID SEARCH UID <last_uid+1>:*` (the range is pushed to the server). A changed
  `UIDVALIDITY` invalidates the stored UID and triggers a full re-scan of that
  folder (dedup keeps it duplicate-free). The watermark lives in Redis only —
  folder names come from the remote server, so mirroring it durably would let a
  malicious server bloat the Postgres row. If Redis is evicted, the next run
  simply re-scans the whole account; dedup keeps that safe.

  The watermark advances **only past a UID that was actually handled** (fetched,
  then delivered or permanently rejected). A UID whose *fetch* fails even after
  retries is treated as transient: the watermark is left below it and the run
  ends as `TransientImportError` — left `is_active`, so the scheduler
  re-dispatches and resumes at that UID rather than silently skipping it. Single
  `FETCH` timeouts are absorbed in-line by a bounded exponential-backoff retry
  (a fixed few attempts on the same connection). Connect-time network failures and unselectable folders
  map to the same `TransientImportError` (never a silent skip — an unselectable
  folder would otherwise let a oneshot complete with its mail missing).

  Transient is not forever: `run_import_task` keeps a **cross-run stuck
  budget** (`STUCK_RETRY_LIMITS`) — too many consecutive runs dying at the
  *same* watermark turn the run into a durable `FAILED` with the underlying
  error, so a permanently broken source surfaces instead of retrying invisibly.
  Any progress (or a completed pass, or a re-arm) resets the budget. The budget
  is **sized by source**: file imports get a few tries (`FILE_STUCK_RETRIES`,
  a brief storage/S3 blip); IMAP is sized from the poll cadence
  (`IMAP_STUCK_TIMEOUT ÷ MESSAGES_IMPORT_IMAP_POLL_INTERVAL`, ~5 days of
  polls) so a continuous poller rides out a multi-day server outage instead of
  disabling itself. (Storage/S3 errors during a *file* import are mapped to the
  same `TransientImportError`, so a bucket blip resumes rather than failing the
  run on the first hiccup.)

### Scope: an importer, not a (full) syncer

Continuous IMAP is a **one-way, append-only importer**, not a two-way mailbox
sync. It only ever pulls **new** messages (`UID > last_uid` per folder) — the
same UID/UIDVALIDITY watermarking a desktop/mobile client uses to detect new
mail. What a real mail client *also* does and we deliberately **do not**:

- **No push (IDLE).** Clients hold a connection open and `IDLE` (RFC 2177) so the
  server pushes `* n EXISTS` the instant mail arrives (near-zero latency, one
  long-lived login). We poll on a fixed cadence
  (`MESSAGES_IMPORT_IMAP_POLL_INTERVAL`) with a fresh connect+login+`SELECT` each
  tick. New-mail latency is therefore up to one poll interval — fine for
  archival import, not for a live mirror.
- **No flag / expunge sync (CONDSTORE/QRESYNC/MODSEQ).** Clients track a per-folder
  `MODSEQ` and use `SELECT … (QRESYNC …)` (RFC 7162) to learn, in one round trip,
  which already-seen messages had **flags changed** (read/starred) or were
  **expunged** (deleted) on the source. We never revisit a UID once imported: a
  message later read, flagged, or deleted upstream is **not** reflected here.
  This is correct for "copy my old mail in once (and keep catching new arrivals)"
  and is the defining difference from maintaining a bidirectional replica.

Adding IDLE (lower latency) or QRESYNC (flag/expunge sync) would move this from
importer toward syncer; both are intentionally out of scope for now.

## Scheduling: crash-recovery and continuous polling

`schedule_imports_task` (periodic Beat task) scans active import channels and
re-dispatches those that are "due" — using the same durable `last_used_at`
heartbeat for both jobs:

- **oneshot**: due when the heartbeat is stale beyond
  `MESSAGES_IMPORT_STALL_TIMEOUT` → a crashed run to resume.
- **continuous IMAP**: due when the heartbeat is older than the global
  `MESSAGES_IMPORT_IMAP_POLL_INTERVAL` seconds → the next poll. The interval is a
  boot-validated positive integer (`values.PositiveIntegerValue`); the whole
  due-check is one SQL query, so one malformed channel can't abort the scan.

The scheduler does **not** force-release the run-lock: a genuinely crashed
holder's lock self-expires within the stall window (lock TTL == stall), while a
live-but-slow run keeps renewing it every message (`beat`), so a redundant
dispatch simply bails on `ALREADY_RUNNING`. That closes the window where
force-releasing a live run's lock allowed a second runner to double-write it.

## Orphaned uploads

Because upload keys are unique per upload (nothing is overwritten), an abandoned
or superseded upload simply leaves a dead object behind. An archive uploaded to
the `message-imports` bucket but never imported (or a multipart upload started
and never completed) is reclaimed by the **bucket's own lifecycle rule** —
object `Expiration` + `AbortIncompleteMultipartUpload`, both set by
`create_bucket` — not by an application task. The lifecycle window matches the
import resume window (7 days).
Once an import has run, its source object is no longer needed: `Message.channel`
holds the delivered mail, and a resume dedups on `mime_id`/sha256.

> The object store must actually apply the lifecycle rule. Some S3-compatible
> stores (e.g. rustfs, the dev store) accept and persist it but don't implement
> `ListMultipartUploads`, so there is no app-side fallback for dangling multipart
> uploads — the native `AbortIncompleteMultipartUpload` rule is the only path.

## Cancelling

Cancel is cooperative and returns immediately. The API flips the run to
`cancelled` (`mark_cancelled`: `is_active=False`, status, **and a Redis cancel
flag**) and dispatches `cancel_import_task` to delete the import's messages and
clean orphaned threads. A run that is *currently executing* polls that flag
every message (`beat`) and unwinds via `ImportCancelled` — so it stops promptly
instead of running to completion and overwriting `cancelled` with `completed`.
`run_import_task` then purges anything it delivered after the API's own purge
snapshot, so no orphaned messages survive. Both the deletion task and the purge
are idempotent.

A cancelled run also disappears from `/imports/` entirely: once the purge has
settled, its channel row is deleted (`_finish_cancelled_run`). Whoever ends the
run removes it — the live worker after its own post-cancel purge, or
`cancel_import_task` when no worker holds the run lock (the row must survive
while a worker is mid-abort, or its late deliveries would be orphaned). A row a
crashed worker leaves behind is harmless (`is_active=False`, hidden by the UI).

The purge spares imported messages whose thread has gathered *non-import*
activity since the import (a reply that arrived, a draft or sent reply from the
app): cancelling undoes the import, but deleting the anchor of a live
conversation would orphan its replies. Messages from other import runs don't
count as activity, so cancelling overlapping imports still cleans everything.

## API

Imports are a mailbox-nested viewset — `IsMailboxAdmin` required:

| Method / path | Action |
|---|---|
| `POST /api/v1.0/mailboxes/{mailbox_id}/imports/` | Start an import. `source=file` needs the `file_key` from the upload endpoint plus `filename`; `source=imap` needs the connection fields. `202` |
| `GET  …/imports/` · `GET …/imports/{id}/` | List / read run state (from `merged_state`) |
| `POST …/imports/{id}/cancel/` | Cancel + purge messages, then remove the run from the list (async). `202` |
| `PATCH …/imports/{id}/` | Change how it runs: `{"mode": "continuous"}` (re-)arms an IMAP poller (add `"is_active": false` to arm it paused), `{"mode": "oneshot"}` demotes one, `{"is_active": false}` pauses one (continuous only — pausing a one-shot would strand a running import with no way to resume it) |
| `DELETE …/imports/{id}/` | Forget a settled run, **keeping** its messages (opposite of cancel). Rejects a running or still-polling run. `204` |

Continuous mode is only valid for `source=imap`: the create serializer rejects
`mode=continuous` for file sources, and `PATCH` rejects it for non-IMAP imports.
The poll cadence itself is the global `MESSAGES_IMPORT_IMAP_POLL_INTERVAL` setting,
not settable per import.

### Uploading a file first

A file import is two steps. The client uploads the archive to the
`message-imports` bucket via `POST /api/v1.0/mailboxes/{mailbox_id}/imports/upload/`
(direct presigned PUT, or `?multipart` for large files with `part/`, complete,
abort sub-calls) — nested under the same mailbox and gated by the same
`IsMailboxAdmin` as the import it feeds — then passes the returned `file_key`
to `POST …/imports/`. The key
is **server-minted and unique per upload** (`<user-prefix>/<uuid>`): nothing in
the bucket is ever overwritten, so a re-upload can't swap the bytes under a
resumable import, and every endpoint re-validates that the key was minted for
the calling user before it touches S3.

## Code map

- `core/services/importer/service.py` — `start_file_import` / `start_imap_import`:
  validate + detect format, create the channel, dispatch the run. Called only by
  the imports API and deliberately does **not** authorize (the viewset's
  `IsMailboxAdmin` owns that); see [permissions.md](permissions.md).
- `core/services/importer/channel.py` — channel creation, Redis state, lifecycle
  transitions (`mark_started`/`record_progress`/`mark_finished`/`heartbeat`),
  run-lock, cancel/purge, continuous controls.
- `core/services/importer/tasks.py` — orchestration: the Celery tasks
  (`run_import_task`, `cancel_import_task`, `schedule_imports_task`) and the
  `_RUNNERS` source→runner dispatch table.
- `core/services/importer/utils.py` — shared, format-agnostic runner
  primitives (`deliver`, `beat`, `imports_storage`, `FLUSH_EVERY`,
  `TransientImportError`). A leaf module so the format runners import it without
  a cycle.
- `core/services/importer/{mbox,eml,pst,imap}.py` — one per source: each owns
  its `run_<format>()` resumable pass plus that format's parsing/indexing
  helpers.

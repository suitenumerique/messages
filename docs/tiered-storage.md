# Tiered blob storage

Blobs (raw RFC822 email bodies and attachments) live in PostgreSQL by
default. Once a blob is older than `MESSAGES_BLOBS_OFFLOAD_DELAY`,
a periodic celery task moves its bytes to S3 and clears the PG row's
`raw_content`. Reads transparently fetch from whichever location the
row points at — application code only ever calls `blob.get_content()`.

## Architecture

- **Storage path**: `blobs/{key_id}/{sha[:3]}/{sha}`. The leading
  `key_id` segment lets blobs encrypted with different keys coexist
  (essential for crash-safe online key rotation). The 3-char sha
  prefix shards each key into 4096 sub-prefixes for S3 request-rate
  balance.
- **Deduplication**: identical content always lands as exactly ONE
  ``Blob`` row, regardless of how many mailboxes / messages /
  attachments reference it. ``BlobManager.create_blob`` hashes the
  input first and short-circuits on a sha-match — no compress, no
  encrypt, no insert. Multiple ``Message`` / ``Attachment`` /
  ``MessageTemplate`` rows can FK the same Blob; cleanup is governed
  by the reference graph + GC sweep below. After offload, the single
  Blob row maps to one S3 object. The DB is the sole source of
  truth; drift between DB and bucket (external deletion, lifecycle
  expiry) is detected offline by
  ``verify_blobs --mode=db-to-storage``, not on the hot path.
- **Concurrency**: a Postgres transaction-scoped advisory lock keyed
  on the first 8 bytes of sha256 serializes offload, cleanup, and
  re-encrypt for any one content. Different shas run in parallel.
- **Encryption**: optional AEAD (currently AES-256-GCM; the algo is
  named per-key in config so future additions don't require a format
  change). Configured secrets are hashed to 32 bytes via SHA-256
  (same pattern as `encrypted-fields`'s `SALT_KEY`). The hash adds
  no entropy — its strength is whatever entropy the operator put
  into the input string. Storage layout per object:
  `nonce(12) || ct+tag(16)`, total overhead 28 bytes, no base64. The
  blob's SHA-256 is bound as AAD on the auth tag, so ciphertext is
  non-portable: copying bytes between blob paths fails decrypt with
  `InvalidTag`.
- **Read-time hash verification (optional)**: set
  `MESSAGES_BLOBS_VERIFY_HASH=True` to re-hash decompressed plaintext
  on every read. Adds one SHA-256 per read. Most useful for
  `key_id=0` (plaintext-stored) blobs — encrypted blobs already get
  the AAD-bound auth tag for free.

## Blob lifetime: reference graph + GC sweep

Blobs are **not owned by a Mailbox/MailDomain** at the schema level.
A Blob is alive as long as any of these references it:

- ``Message.blob`` (the raw RFC822 MIME) or ``Message.draft_blob``
  (the body of a draft being composed)
- ``Attachment.blob`` (per-attachment during draft composition)
- ``MessageTemplate.blob`` (signatures, autoreply bodies)
- ``InboundMessage.blob`` (in-flight internal message)

Plus a short-lived **upload reservation** in the form of a
``MailboxBlob`` row carrying an explicit ``expires_at`` timestamp. The
JMAP upload endpoint creates one alongside the ``Blob`` row so the
blob_id survives until the follow-up attach call lands; the attach
flow drops it once the ``Attachment`` row exists.

When a reference source is deleted (Message, Attachment,
MessageTemplate, InboundMessage ``post_delete``), the affected blob_id
is pushed into a Redis candidate set. A periodic Celery task —
``gc_orphan_blobs_task`` in ``core/services/blob_gc.py`` — drains the
set, re-checks the reference graph under the per-sha advisory lock,
deletes the row if no references remain, and cleans up the S3
object inline. No per-blob celery fan-out; one task processes the
whole backlog within a 55-minute wall-clock budget per hourly tick.

Two modes:

- ``mode="fast"`` (default, beat-scheduled hourly): drain the Redis
  candidate set, GC anything that's actually orphaned.
- ``mode="full"``: walk every Blob row. Use as a periodic safety net
  (weekly cron) to catch anything dropped by a Redis outage or a
  signal that didn't fire. Invoke manually with
  ``python manage.py run_task core.services.blob_gc.gc_orphan_blobs_task --kwargs '{"mode": "full"}'``.

Pass ``"dry_run": true`` to preview without deleting:
``python manage.py run_task core.services.blob_gc.gc_orphan_blobs_task --kwargs '{"mode": "full", "dry_run": true}'``.
The task logs one INFO line per would-be-deleted blob (id, sha256,
storage_location, size, created_at) and returns counts with
``would_delete`` instead of ``deleted``. Nothing is locked or mutated.
In ``fast`` mode, dry-run peeks at the Redis candidate set rather than
popping it, so a follow-up real run still sees the same ids. The result
is informational: a concurrent reference insert between dry-run and a
real run could turn a would-delete into a skip.

The GC-driven model fixes a latent bug from the FK-cascade era: when
two mailboxes shared a thread, deleting one mailbox would CASCADE
through ``Blob.mailbox`` and break content access for the other
mailbox. Without the FK, the blob is alive as long as any thread
access still references it.

## Enabling offload

By default everything runs in PostgreSQL with `raw_content` populated.
To start moving cold blobs to S3:

1. Provision a bucket and credentials.
2. Set the `STORAGE_MESSAGE_BLOBS_*` env vars (see [env.md](env.md)).
3. `make create-buckets` (or the production equivalent).
4. (Optional) Preview what the next tick would offload:
   ``python manage.py run_task core.services.tiered_storage_tasks.offload_blobs_task --kwargs '{"dry_run": true}'``.
   The task logs one INFO line per eligible blob (id, size, stored, content_type, created_at) and returns counts with ``would_offload`` plus ``bytes_plain`` / ``bytes_stored``. Bypasses both the ``MESSAGES_BLOBS_OFFLOAD_ENABLED`` and ``service.enabled`` gates so you can preview before configuring the bucket.
5. Set `MESSAGES_BLOBS_OFFLOAD_ENABLED=True`.

A single celery beat task fires hourly and processes eligible blobs
sequentially within a 55-minute wall-clock budget — no per-blob
fan-out. Each row is offloaded under the per-sha advisory lock, so
the row flip is atomic. Whatever isn't done in one tick is picked up
by the next.

## Encryption

To enable encryption-at-rest for blobs:

1. Generate a random secret string. Use `openssl rand` or equivalent
   so the input is genuinely high-entropy:
   ```sh
   openssl rand -base64 32
   ```
   Startup emits a warning if any configured secret is shorter than
   32 characters. The warning is a length check, not an entropy
   measurement — `"a" * 32` passes silently. Treat the floor as a
   tripwire for typos, not a security guarantee.
2. Add it to `MESSAGES_BLOBS_ENCRYPT_KEYS` as a JSON dict. Every
   entry must spell out `algo` and `secret`. Add `"active": true`
   to the entry whose key new blobs should be encrypted with:
   ```sh
   MESSAGES_BLOBS_ENCRYPT_KEYS='{"1": {"algo": "aes-gcm", "secret": "<the secret>", "active": true}}'
   ```
   Entries without `active` (or with `active: false`) stay readable
   for legacy ciphertext but no longer encrypt new blobs. At most
   one entry may be active. The `algo` value is a complete spec —
   picking a new cipher in the future means adding a new algo
   identifier, not changing what the current one means. Unknown
   algos are rejected at boot and at use time.
3. Restart. New blobs are encrypted; existing unencrypted blobs
   (`encryption_key_id=0`) keep working until they're rotated.

`manage.py check` validates the config at startup and refuses to
boot if more than one entry is active, or any algo is unknown.

**Lose the key dict, lose the data.** Back it up alongside your DB.

**Rotate before you hit AES-GCM's safety limit.** With a random
96-bit nonce per encryption, the standard guidance is to retire a
key after 2^32 (≈ 4 billion) encryptions to keep the
nonce-collision probability negligible. Plan a rotation at-or-below
that count even if no key compromise is suspected; the
[`re_store_blobs` runbook](#key-rotation-runbook) below walks through
the steps. (One blob = one encryption, regardless of how many DB
rows reference it via dedup.)

## Reconciling state: `re_store_blobs`

`re_store_blobs` makes every blob's state match the
current configuration. Two operations, applied based on settings:

- **Key rotation** (always). Every row whose `encryption_key_id`
  differs from the entry currently flagged `active=true` in
  `MESSAGES_BLOBS_ENCRYPT_KEYS` is re-encrypted under that key.
- **Restore** (only when `MESSAGES_BLOBS_OFFLOAD_ENABLED=False`).
  Every `OBJECT_STORAGE` row is pulled back into PostgreSQL.

Two runbooks below use this command.

### Key rotation runbook

To rotate from key 1 to key 2 while keeping tiered storage enabled:

1. Add the new key alongside the old one, with `active=true` only on
   the new entry:
   ```sh
   MESSAGES_BLOBS_ENCRYPT_KEYS='{"1": {"algo": "aes-gcm", "secret": "<old>"}, "2": {"algo": "aes-gcm", "secret": "<new>", "active": true}}'
   ```
   New blobs encrypt under key 2 immediately; existing blobs stay on
   key 1 until rotated.
2. **Pause offload to avoid a race**:
   ```sh
   MESSAGES_BLOBS_OFFLOAD_ENABLED=False
   ```
   Wait for in-flight tasks to drain.

   ⚠️ Note: with offload disabled, `re_store_blobs` will *also* pull
   OBJECT_STORAGE blobs back into PostgreSQL. If you only want to
   rotate keys and keep blobs in S3, leave `MESSAGES_BLOBS_OFFLOAD_ENABLED=True`
   and accept that an offload task could race the rotation (both
   take the per-sha advisory lock, so the worst case is a few
   re-tries — no corruption).
3. Run the rotation:
   ```sh
   python manage.py re_store_blobs
   ```
   For each blob with `encryption_key_id != 2`, the command
   - writes the new ciphertext to `blobs/2/...` (atomic S3),
   - flips the dedup cohort's `encryption_key_id` to 2 (atomic DB),
   - best-effort deletes the old `blobs/1/...` path.

   Each step is independently atomic, so a crash at any point leaves
   the blob readable from one consistent path.
4. Confirm key 1's prefix is empty:
   ```sh
   aws s3 ls s3://msg-blobs/blobs/1/
   ```
   If anything remains, re-run the command (it's idempotent) or use
   `verify_blobs --mode=storage-to-db` to list orphans for
   manual cleanup.
5. Re-enable offload, then drop key 1 from the dict in a follow-up
   deploy:
   ```sh
   MESSAGES_BLOBS_OFFLOAD_ENABLED=True
   MESSAGES_BLOBS_ENCRYPT_KEYS='{"2": {"algo": "aes-gcm", "secret": "<new>", "active": true}}'
   ```

### Rolling tiered storage back

To bring every offloaded blob back into PostgreSQL:

1. Disable offload so no new blobs leave PG:
   ```sh
   MESSAGES_BLOBS_OFFLOAD_ENABLED=False
   ```
   Wait for in-flight offload tasks to drain (next beat tick is
   the kill switch — already-running tasks finish their current
   blob, then the loop's `disabled` check makes the next tick a
   no-op).
2. Verify your PG has room. Each row that comes back from S3
   carries its own copy of the (compressed, encrypted) bytes —
   dedup that was sharing a single S3 object becomes one PG row
   per blob. Check `pg_total_relation_size('messages_blob')` ahead
   of time.
3. Run the restore. Bucket creds must still be configured so
   ciphertext can be read on the way home:
   ```sh
   python manage.py re_store_blobs
   ```
   For every `OBJECT_STORAGE` row the command:
   - downloads the ciphertext, decrypts under its current key,
     re-encrypts under the active key,
   - writes `raw_content` and flips `storage_location=POSTGRES`
     (atomic DB),
   - opportunistically deletes the S3 object once the cohort
     empties out.
4. Confirm the bucket is empty:
   ```sh
   aws s3 ls s3://msg-blobs/blobs/ --recursive
   ```
   Anything still there is an orphan — `verify_blobs --mode=storage-to-db`
   lists them.
5. (Optional) Tear down bucket creds. Once
   ```sh
   SELECT count(*) FROM messages_blob WHERE storage_location=2;
   ```
   returns 0, you can safely unset `STORAGE_MESSAGE_BLOBS_*` —
   no read path will need them.

## Operator commands

Two commands cover the operational surface; they are deliberately
split so the read-only audit can never touch state and the mutating
reconciliation can never be run by accident as part of "I just want
to see what's going on":

- **`python manage.py verify_blobs`** — read-only audit. Nothing is
  mutated; output drives manual recovery.
- **`python manage.py re_store_blobs`** — mutating reconciliation
  (key rotation + optional pull-back from S3 → PG). See
  [the runbook section](#reconciling-state-re_store_blobs) above.

### `verify_blobs` (read-only)

Choose a mode with `--mode=<name>`. The default is `full` which
runs both checks.

- **`--mode=db-to-storage`** — for every blob row marked
  `OBJECT_STORAGE`, HEAD the expected S3 object. Reports `MISSING`
  for any drift (DB says present, bucket says absent).
- **`--mode=storage-to-db`** — LIST every object under `blobs/` and
  look up the matching DB row. Reports `ORPHAN` for any S3 object
  not referenced by any row, and `INVALID PATH` for objects whose
  key doesn't match the `blobs/{key_id}/{sha[:3]}/{sha}` shape.
- **`--mode=full`** (default) — runs `db-to-storage` then
  `storage-to-db` back-to-back.
- **`--verify-hashes`** — additional flag. When combined with
  `storage-to-db` or `full`, downloads each S3 object, decrypts,
  decompresses, and recomputes SHA-256. Reports `HASH MISMATCH` on
  divergence. Slow; use after a storage incident or as periodic
  paranoia. Silently no-op when used with `--mode=db-to-storage`.
- **`--limit=N`** — caps items checked (useful for sampling).
- **`--start-after-key=<key>`** — resumes a `storage-to-db` listing
  past the given key. Pair with `--limit` to chunk a multi-million-
  object bucket across runs.

### `re_store_blobs` (mutating)

Reads each blob, decrypts under its current key, re-encrypts under
the active key, and writes the result to the target storage
location implied by `MESSAGES_BLOBS_OFFLOAD_ENABLED`. The PG → S3
direction (offload of cold blobs) is owned by the periodic
`offload_blobs_task` celery beat task — not exposed here.

- **`--dry-run`** — prints what would happen without writing.
- **`--limit=N`** — caps the worklist; rerun until empty.

## Recovery scenarios

| Symptom | Detection | Fix |
|---|---|---|
| DB row says `OBJECT_STORAGE` but S3 object missing | `verify --mode=db-to-storage` reports `MISSING` | Restore from backup (re-upload the bytes to the bucket and re-run `verify --mode=db-to-storage` to confirm). If no backup exists, re-ingest the original content from upstream (the producing source — IMAP, MTA, importer — or application logs that captured it). If neither is possible the blob is unrecoverable; mark the row with an incident annotation rather than fabricating bytes from another row. |
| S3 object with no DB row | `verify --mode=storage-to-db` reports `ORPHAN` | After confirming, `aws s3 rm` the object. |
| Hash mismatch | `verify --verify-hashes` reports `HASH MISMATCH` | The S3 object is corrupted (or AAD-bound integrity has been broken — same effect). Treat as data loss; restore from backup. |
| Old key_id prefix non-empty after rotation | `aws s3 ls blobs/<old>/` | Re-run `re_store_blobs`; it's idempotent. |
| `re_store_blobs` reports errors on a subset of blobs | command exit summary lists `Errors: N` | The loop already skipped them and continued; failed blobs stay in their pre-run state. Re-run after fixing the underlying cause (key missing, S3 5xx, etc.); the command is idempotent. |
| Suspect orphan Blob rows accumulating (e.g. after a Redis outage) | `SELECT count(*) FROM messages_blob` significantly higher than the sum of ``Message.blob``, ``Message.draft_blob``, ``Attachment.blob``, ``MessageTemplate.blob`` distinct ids | Run the full GC: ``python manage.py run_task core.services.blob_gc.gc_orphan_blobs_task --kwargs '{"mode": "full"}'``. It walks every Blob row and deletes anything without a remaining reference. Idempotent. |

## Schema migration is one-way

Migration `0027` drops `Blob.mailbox` / `Blob.maildomain` and the
`blob_has_owner` constraint, and flips every Blob-referencing FK
(`Message.blob`, `Message.draft_blob`, `Attachment.blob`,
`MessageTemplate.blob`, `MailboxBlob.blob`) to `on_delete=PROTECT`.
The FK drop is intentionally **not reversible**: once those columns
are gone, there is no way to repopulate them from the reference
graph (a blob shared across mailboxes via dedup has no single
"owner" to write back). The `on_delete=PROTECT` flips are reversible
at the SQL level (just metadata), but rolling back re-introduces
the data-loss races the change fixed. Treat the migration as
one-way for production.

## Operational notes

- **Initial rollout on a populated DB.** The offload task processes
  eligible blobs sequentially within a 55-minute wall-clock budget per
  hourly tick. There is no per-blob celery fan-out, so a backlog of
  millions doesn't queue-bomb the broker. Ramping
  `MESSAGES_BLOBS_OFFLOAD_DELAY` down gradually (e.g. 365d → 90d → 30d
  → 1d, expressed in seconds) still helps spread the load over several
  days, especially if
  bandwidth to the bucket is the bottleneck. Run `--mode=db-to-storage`
  periodically afterwards to confirm everything that flipped to
  `OBJECT_STORAGE` is actually in the bucket.
- **Reading offloaded blobs** triggers a synchronous S3 GET in the
  request path. Old messages are rarely re-opened, so this is
  generally invisible — but expect 50–500 ms latency when it does
  happen.
- **Bulk deletes** (e.g. mailbox cascade, `QuerySet.delete()`) push
  the affected blob_ids into the GC candidate set via post_delete
  signals on the *reference sources* (Message, Attachment, Template).
  No per-blob celery task is enqueued — the periodic GC drains the
  set in a single bounded task per tick. Cascade of 100k attachments
  produces 100k Redis SADDs (~1-2s total), not 100k Celery messages.
- **Drift between DB and bucket.** Offload trusts the DB on dedup
  (no S3 HEAD), so a missing-S3-but-DB-says-OBJECT_STORAGE row will
  not auto-repair. Run `verify_blobs --mode=db-to-storage`
  on a schedule (weekly, monthly — workload-dependent) to catch
  drift; the recovery scenarios table above maps each symptom to a
  fix.
- **Postgres disk growth.** Offload nulls `raw_content`, it doesn't
  delete the row, so TOAST stabilizes around the un-offloaded
  backlog while heap and non-partial indexes (`pkey`, `sha256`)
  grow with the cumulative row count. The offload `UPDATE` changes
  `storage_location` (an indexed column), so HOT updates are
  disabled and each offload writes into every index on the table.
  At high write rates default autovacuum can fall behind on index
  and TOAST bloat. Monitor `dead_pct` and `pg_total_relation_size`
  for `messages_blob` over time; if growth or dead-tuple ratio
  trend upward after autovacuum catches up, tighten per-table
  autovacuum and add a periodic online compaction
  (e.g. `pg_repack`) to the runbook. Partitioning by `created_at`
  is the lever to keep in mind once row count gets large.

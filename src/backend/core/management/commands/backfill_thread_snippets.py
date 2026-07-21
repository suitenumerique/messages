"""Progressively backfill ``Thread.snippet`` for pre-existing threads.

New activity re-derives a thread's snippet automatically via
``Thread.update_stats``; this command rebuilds the dormant backlog by parsing
only the latest visible message of each thread — one blob read per thread, not
per message.

``--reset`` clears stored snippets and exits — the rebuild is a separate,
deliberate run. Snippets predating the current
derivation carry raw HTML/markdown or a duplicated subject, and a deployment
sitting on those wants them gone before it turns ``FEATURE_THREAD_SNIPPET``
on. That purge lives here rather than in a migration because it is
irreversible and release-specific: only the deployments that want a rebuild
should pay for one. It does not chain into the refill because the purge is
table-wide while the refill is bounded by ``--limit`` — combined, one run
would silently leave everything past the limit empty; sequenced, the operator
sizes the refill knowingly.

Sized for six-figure backlogs. Three things dominate at that scale, and each
has a lever here:

* the keyset scan, which pages on ``(created_at, id)`` instead of ``OFFSET`` so
  a batch never re-reads what earlier batches already returned. No index backs
  it — ``messages_thread`` has none on ``created_at`` — so each batch still
  costs a sequential scan; keep ``--limit`` sized accordingly and prefer few
  large runs over many small ones;
* the per-thread lookups, collapsed into a single ``DISTINCT ON`` query per
  batch — the naive form costs two round-trips per thread (the message, then
  its lazily-loaded blob), i.e. well over a million on a 700k backlog;
* the blob reads, which once blobs are offloaded to object storage are network
  round-trips rather than CPU, so ``--concurrency`` overlaps them. This is the
  single biggest win and it is opt-in: the default of 1 keeps the load profile
  of a routine cron run unchanged. ``--concurrency`` also bounds how many
  bodies are ever resident at once — the how and why live on
  ``_resolve_messages`` and ``_load_pending_content``.

Runs in bounded batches so it can be repeated (e.g. from cron) instead of one
job that reads every thread at once. Threads are filled newest-first: a bounded
run covers the threads still sitting at the top of the lists users actually
look at, so the visible payoff comes with the first run rather than the last.
Each batch prints the keyset cursor it reached *and* stores it in the cache, so
consecutive runs walk the backlog once end to end instead of restarting from
the top. That is what keeps the threads which legitimately keep ``snippet=""``
— draft-only threads, bodyless messages, unreadable blobs — from being re-read
on every run: they never leave the queryset, so without a cursor that outlives
the run they accumulate at the head of the scan until a cron run spends its
whole budget re-parsing them before reaching any fillable thread. ``--restart``
drops the cursor to scan from the top again, ``--resume-from`` overrides it for
one run, and ``--before`` fences the pre-deploy backlog by creation date.

Spam-only threads are deliberately out of scope: they are excluded by
``has_messages`` (which ``update_stats`` computes ignoring spam) even though
``update_stats`` would give them a snippet. Dropping that filter to reach them
would also pull in the fully-trashed threads still sitting at ``snippet=""``
(``update_stats`` keeps whatever snippet a fully-trashed thread has, and
derives nothing for it) — and this command cannot fill them either, so every
scan would pay to walk them for nothing.

Usage:
    python manage.py backfill_thread_snippets              # one bounded run
    python manage.py backfill_thread_snippets --limit 700000 --concurrency 16
    python manage.py backfill_thread_snippets --resume-from '2026-07-01T09:00:00+00:00,3f2b…'
    python manage.py backfill_thread_snippets --before 2026-07-01 --dry-run
    python manage.py backfill_thread_snippets --restart    # rescan from the top
    python manage.py backfill_thread_snippets --reset      # clear only, then exit
"""

import datetime
import logging
import os
import signal
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Case, Q, Value, When
from django.utils.dateparse import parse_datetime

from core import models
from core.enums import BlobStorageLocationChoices
from core.mda.utils import message_snippet

logger = logging.getLogger(__name__)

# One key for the whole backlog, deliberately not namespaced per invocation:
# the scan walks the same table whatever bounds a given run carries, and
# keying on them would silently restart it whenever an operator retunes
# ``--limit`` or ``--concurrency``.
_CURSOR_CACHE_KEY = "backfill_thread_snippets:cursor"


def _chunks(items, size):
    """Yield ``items`` in consecutive slices of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _rss_mb():
    """Return this process' resident set size in MB, 0 where unavailable.

    Reported per batch because the failure mode this command has to stay clear
    of is the OOM killer, which leaves no traceback: a growing figure here is
    the only warning you get before the process disappears.
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            pages = int(statm.read().split()[1])
    except (OSError, ValueError, IndexError):
        return 0.0
    return pages * os.sysconf("SC_PAGE_SIZE") / 1e6


def _parse_cutoff(raw, label):
    """Coerce an ISO date/datetime string into an aware UTC datetime."""
    cutoff = parse_datetime(raw)
    if cutoff is None:
        try:
            # Bare date → midnight UTC.
            cutoff = datetime.datetime.fromisoformat(raw).replace(tzinfo=datetime.UTC)
        except ValueError as exc:
            raise CommandError(
                f"{label} {raw} is not a valid ISO date/datetime"
            ) from exc
    elif cutoff.tzinfo is None:
        # Datetime without offset → assume UTC.
        cutoff = cutoff.replace(tzinfo=datetime.UTC)
    return cutoff


def _parse_cursor(raw):
    """Split a ``<created_at>,<thread_id>`` cursor into its keyset parts.

    ``rpartition`` rather than ``split``: an ISO datetime never contains a
    comma, but being explicit about which side is the UUID keeps a stray
    separator from silently truncating the timestamp.
    """
    created_at_raw, _, id_raw = raw.rpartition(",")
    if not created_at_raw or not id_raw:
        raise CommandError(
            f"--resume-from {raw} must be '<created_at>,<thread_id>' — copy the "
            "cursor printed by the previous run"
        )
    try:
        thread_id = uuid.UUID(id_raw.strip())
    except ValueError as exc:
        raise CommandError(
            f"--resume-from thread id {id_raw.strip()} is not a UUID"
        ) from exc
    return _parse_cutoff(created_at_raw.strip(), "--resume-from"), thread_id


def _read_cursor():
    """The keyset cursor left by the previous run, or ``(None, None)``."""
    raw = cache.get(_CURSOR_CACHE_KEY)
    if not raw:
        return None, None
    try:
        return _parse_cursor(raw)
    except CommandError:
        # A malformed leftover must not wedge every subsequent run: start over
        # rather than abort — re-scanning is slow, refusing to run is worse.
        logger.warning(
            "backfill_thread_snippets: ignoring unparsable cached cursor %r", raw
        )
        return None, None


def _write_cursor(created_at, thread_id):
    """Persist the keyset cursor so the next run starts where this one stopped.

    Stored in the same ``'<created_at>,<thread_id>'`` form ``--resume-from``
    takes, so what the cache holds is exactly what an operator can paste back.

    ``timeout=None`` (never expires): a six-figure backlog walked by cron spans
    days, and a cursor expiring mid-scan would silently restart it from the
    top — the very re-scan this exists to prevent. It outlives the backfill on
    purpose too, so the cron runs that follow an exhausted backlog keep costing
    one empty query instead of a full re-walk; ``--reset`` and ``--restart``
    are what clear it.
    """
    cache.set(_CURSOR_CACHE_KEY, f"{created_at.isoformat()},{thread_id}", timeout=None)


def _derive(message):
    """Return ``(snippet, error)`` for one message; never raises.

    Runs on the worker pool, so it MUST NOT touch the ORM: Django connections
    are thread-local and a lazily-loaded field here would open (and leak) one
    connection per worker — and under a transaction it would not even see the
    rows it asks for. ``_load_pending_content`` has already populated every
    PostgreSQL-resident body on the main thread, which is what keeps this
    ORM-free. Object storage reads are safe here — django-storages holds its
    boto3 resource in a ``threading.local``.
    """
    try:
        return message_snippet(message.get_parsed_data()), None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # A single unreadable/corrupt blob must not abort the run.
        return None, exc


class _GracefulStop:
    """Context manager flipping a flag on SIGINT/SIGTERM.

    A 700k run lasts hours and will meet a deployment sooner or later. Without
    this, the SIGTERM kills the process mid-batch and the keyset cursor — the
    only way to resume without re-reading everything — is lost. Here the
    current batch finishes, the cursor is printed, and the command exits.
    """

    def __init__(self):
        self.requested = False
        self._previous = {}

    def __enter__(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous[sig] = signal.signal(sig, self._handle)
            except ValueError:
                # Not the main thread (test runner, embedded call): the run
                # simply stays uninterruptible.
                pass
        return self

    def _handle(self, signum, frame):  # pylint: disable=unused-argument
        """Request a stop at the next batch boundary."""
        self.requested = True

    def __exit__(self, *exc_info):
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        return False


class Command(BaseCommand):
    """Backfill Thread.snippet from each thread's latest visible message."""

    help = "Populate Thread.snippet from the latest visible message, in batches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=10000,
            help="Max threads to process this run (default: 10000).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help=(
                "Threads fetched per keyset page, and one write per page "
                "(default: 500). Does not affect memory — bodies are read "
                "--concurrency at a time, not per page."
            ),
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=1,
            help=(
                "Blob reads to overlap (default: 1, sequential). Raise to "
                "8-16 when blobs live in object storage — the work is network "
                "bound, not CPU bound. Doubles as the memory dial: bodies are "
                "read this many at a time, so peak usage is roughly "
                "concurrency x --max-blob-size, independent of --batch-size."
            ),
        )
        parser.add_argument(
            "--max-blob-size",
            type=int,
            default=0,
            help=(
                "Skip messages whose blob exceeds this size in MB (default: 0, "
                "no limit). Decided on metadata alone, before a single content "
                "byte is fetched: an attachment-heavy message costs megabytes "
                "to read and parse for the same 140 characters."
            ),
        )
        parser.add_argument(
            "--before",
            type=str,
            default=None,
            help=(
                "Only backfill threads created before this ISO date/datetime. "
                "Use to target the pre-deploy backlog and skip fresh threads."
            ),
        )
        parser.add_argument(
            "--resume-from",
            type=str,
            default=None,
            help=(
                "Keyset cursor '<created_at>,<thread_id>' to resume past, as "
                "printed by a previous run. Overrides the cursor the previous "
                "run left in the cache."
            ),
        )
        parser.add_argument(
            "--restart",
            action="store_true",
            help=(
                "Drop the cached cursor and scan from the newest thread again. "
                "Use after a --reset, or to re-attempt the threads a previous "
                "run stepped over."
            ),
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help=(
                "Seconds to pause between batches (default: 0). Throttles the "
                "run so a long backfill leaves headroom for live traffic."
            ),
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Clear stored snippets and exit — no rebuild (see the module "
                "docstring for why the refill is a separate run). Honours "
                "--before and --dry-run; ignores --limit, which bounds only "
                "rebuild runs."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        batch_size = options["batch_size"]
        concurrency = options["concurrency"]
        dry_run = options["dry_run"]
        sleep = options["sleep"]
        max_blob_size = options["max_blob_size"] * 1024 * 1024

        if min(limit, batch_size, concurrency) < 1:
            raise CommandError("--limit, --batch-size and --concurrency must be >= 1")

        cutoff = (
            _parse_cutoff(options["before"], "--before") if options["before"] else None
        )

        base_qs = models.Thread.objects.filter(snippet="", has_messages=True)
        if cutoff is not None:
            base_qs = base_qs.filter(created_at__lt=cutoff)

        if options["reset"]:
            cleared = self._reset_snippets(cutoff, dry_run, batch_size)
            if not dry_run:
                # The purge refills the backlog *behind* wherever the cursor
                # stands, so keeping it would leave every thread it already
                # walked past empty forever.
                cache.delete(_CURSOR_CACHE_KEY)
            self.stdout.write(
                self.style.WARNING(
                    f"backfill_thread_snippets: reset {cleared} snippet(s)"
                    f"{' (dry-run)' if dry_run else ''}"
                )
            )
            # Clear only, never chain the rebuild (module docstring).
            return

        # Keyset cursor over ``(created_at, id)``, walked descending. It is
        # what makes the scan monotonic across runs: progress is "the cursor
        # moved past this thread", not "this thread left the queryset". The
        # threads that legitimately keep ``snippet=""`` — bodyless messages,
        # unreadable blobs, draft-only threads — would otherwise be re-read on
        # every single cron run, piling up at the head of the scan until a run
        # spends all its budget on them before reaching any real work.
        last_ct, last_id = None, None
        if options["restart"]:
            cache.delete(_CURSOR_CACHE_KEY)
        if options["resume_from"]:
            last_ct, last_id = _parse_cursor(options["resume_from"])
        elif not options["restart"]:
            last_ct, last_id = _read_cursor()
            if last_id is not None:
                self.stdout.write(
                    f"resuming past cached cursor '{last_ct.isoformat()},{last_id}'"
                )

        exhausted = False
        scanned = populated = errors = skipped = 0
        started = time.monotonic()

        with (
            _GracefulStop() as stop,
            ThreadPoolExecutor(max_workers=concurrency) as pool,
        ):
            while scanned < limit and not stop.requested:
                batch_started = time.monotonic()
                take = min(batch_size, limit - scanned)
                qs = base_qs
                if last_ct is not None:
                    # Split deliberately into a range bound AND a residual OR,
                    # rather than the equivalent-looking
                    # ``created_at < ct OR (created_at = ct AND id < id)``.
                    # PostgreSQL can only turn a leading conjunct into a range
                    # bound, so the hoisted form is the one an index on
                    # ``created_at`` could serve as a seek; the pure OR form
                    # can only ever be a filter. The residual OR then discards
                    # just the handful of rows sharing ``ct``.
                    qs = qs.filter(created_at__lte=last_ct).filter(
                        Q(created_at__lt=last_ct) | Q(id__lt=last_id)
                    )
                batch = list(
                    qs.only("id", "created_at", "snippet").order_by(
                        "-created_at", "-id"
                    )[:take]
                )
                if not batch:
                    # Backlog walked to the end. The cursor is deliberately
                    # KEPT, not cleared: this is the steady state a cron run
                    # lands in, and dropping it here would send every
                    # subsequent run back over the whole residue of threads
                    # that legitimately stay empty — the exact re-scan the
                    # cursor exists to prevent. Threads that appear below it
                    # afterwards (a backdated import) are reached with
                    # ``--restart``, and get a snippet from ``update_stats``
                    # on their own anyway.
                    exhausted = True
                    break

                todo, oversized = self._resolve_messages(batch, max_blob_size)
                skipped += oversized
                # Cursor and scan count advance over the *whole* batch, NOT
                # only over rows that yielded a snippet: in --dry-run and for
                # threads whose latest message has an empty body, a scanned
                # row keeps ``snippet=""``, so paging driven by rows leaving
                # the queryset would re-fetch the same first batch forever.
                scanned += len(batch)
                last_ct, last_id = batch[-1].created_at, batch[-1].id

                to_update = []
                # Bodies are read ``--concurrency`` at a time, not batch-wide
                # — see ``_load_pending_content``.
                for chunk in _chunks(todo, concurrency):
                    self._load_pending_content(chunk)
                    for (thread, message), (snippet, error) in zip(
                        chunk,
                        pool.map(_derive, [message for _, message in chunk]),
                        strict=True,
                    ):
                        if error is not None:
                            errors += 1
                            logger.error(
                                "backfill_thread_snippets: failed to read %s",
                                message.id,
                                exc_info=error,
                            )
                            continue
                        if snippet:
                            thread.snippet = snippet
                            to_update.append(thread)
                    self._release_content(chunk)

                # Guarded on ``snippet=""`` — the very predicate the batch was
                # selected on — because reading the bodies takes seconds, and a
                # message landing on one of these threads meanwhile has
                # ``update_stats`` derive a fresher snippet from it.
                if dry_run:
                    populated += len(to_update)
                elif to_update:
                    populated += models.Thread.objects.filter(
                        id__in=[thread.id for thread in to_update], snippet=""
                    ).update(
                        snippet=Case(
                            *[
                                When(id=thread.id, then=Value(thread.snippet))
                                for thread in to_update
                            ]
                        )
                    )

                # Persisted per batch, and only once the batch's snippets are
                # committed: a cursor written ahead of the write would, on a
                # crash in between, hand the next run a position past threads
                # that were never filled. Never under --dry-run — a rehearsal
                # that moved the cursor would make the next real run skip
                # everything it just pretended to do.
                if not dry_run:
                    _write_cursor(last_ct, last_id)

                self.stdout.write(
                    f"  scanned={scanned} populated={populated} errors={errors} "
                    f"skipped={skipped} "
                    # Per batch, not since the run started: a cumulative
                    # average keeps climbing through a slowdown and hides it.
                    f"rate={len(batch) / max(time.monotonic() - batch_started, 1e-9):.0f}/s "
                    f"rss={_rss_mb():.0f}MB "
                    f"resume-from='{last_ct.isoformat()},{last_id}'"
                )
                # Flushed per batch: piped to a file or a log collector stdout
                # is block-buffered, and a SIGKILL (OOM) or a SIGTERM that
                # outruns the handler would discard the very cursor needed to
                # resume the run.
                self.stdout.flush()

                if sleep:
                    time.sleep(sleep)

        self.stdout.write(
            self.style.SUCCESS(
                f"backfill_thread_snippets: scanned={scanned} populated={populated} "
                f"errors={errors} skipped={skipped} "
                f"in {time.monotonic() - started:.0f}s"
                f"{' (dry-run)' if dry_run else ''}"
                f"{' (interrupted)' if stop.requested else ''}"
                f"{' (backlog exhausted)' if exhausted else ''}"
            )
        )
        if last_id is not None:
            self.stdout.write(f"resume-from: '{last_ct.isoformat()},{last_id}'")

    @staticmethod
    def _load_pending_content(chunk):
        """Fetch ``raw_content`` for the chunk's PostgreSQL-resident blobs.

        Deferred by the batch query and pulled here instead, for two reasons:
        peak memory stays bounded by the chunk rather than by ``--batch-size``,
        and the fetch happens on the main thread so :func:`_derive` can stay
        ORM-free on the workers.

        Offloaded blobs are skipped — their content is NULL in PostgreSQL and
        comes from object storage inside ``get_content``.

        One blob id maps to a *list* of instances, not one: blobs are
        deduplicated by content hash, so messages sharing a body (auto-replies,
        notifications, bulk sends) all point at the same row while
        ``select_related`` still builds a separate instance per message. Filling
        only one of them would leave the others deferred, and they would each
        fault back to the database from a worker thread.
        """
        pending = defaultdict(list)
        for _, message in chunk:
            if (
                message.blob_id
                and message.blob.storage_location == BlobStorageLocationChoices.POSTGRES
            ):
                pending[message.blob_id].append(message.blob)
        if not pending:
            return
        for blob_id, raw_content in models.Blob.objects.filter(
            id__in=pending
        ).values_list("id", "raw_content"):
            # The same bytes object is shared by every instance — no copy.
            for blob in pending[blob_id]:
                blob.raw_content = raw_content

    @staticmethod
    def _reset_snippets(cutoff, dry_run, batch_size):
        """Clear stored snippets so the rebuild can re-derive them.

        Returns the number cleared (or, under ``--dry-run``, that would be).
        ``update_stats`` only re-derives when ``messaged_at`` moves, so an
        emptied snippet stays empty until new activity — refilling is the
        rebuild run's job, not a side effect threads drift back from.

        Keyset-paginated and committed per page rather than one table-wide
        ``UPDATE``: row locks are released as it advances instead of piling up
        against live traffic for the whole purge. Idempotent — cleared rows
        leave the queryset, so an interrupted reset simply resumes.
        """
        qs = models.Thread.objects.exclude(snippet="")
        if cutoff is not None:
            qs = qs.filter(created_at__lt=cutoff)
        if dry_run:
            return qs.count()

        cleared = 0
        last_id = None
        while True:
            page = qs if last_id is None else qs.filter(id__gt=last_id)
            ids = list(page.order_by("id").values_list("id", flat=True)[:batch_size])
            if not ids:
                break
            cleared += models.Thread.objects.filter(id__in=ids).update(snippet="")
            last_id = ids[-1]
        return cleared

    @staticmethod
    def _release_content(chunk):
        """Drop each message's parsed body and blob bytes once its snippet is out.

        The counterpart to ``_load_pending_content``, and the half that
        actually bounds memory. ``todo`` outlives the chunks it is walked in,
        so without this a batch still ends up holding every message it
        processed — parsed email plus raw blob — and peak usage tracks
        ``--batch-size`` no matter how small the chunks are. Measured over 500
        messages of ~150 KB: 137 MB retained without it.
        """
        for _, message in chunk:
            message.discard_parsed_data()
            if message.blob_id:
                # Back to deferred; a later read would simply re-fetch.
                message.blob.__dict__.pop("raw_content", None)

    @staticmethod
    def _resolve_messages(batch, max_blob_size):
        """Pair each thread of ``batch`` with the message to derive from.

        Returns ``(todo, oversized_count)``.

        One ``DISTINCT ON`` query resolves the latest visible message of every
        thread at once, blob included, replacing two queries per thread. The
        ``id`` tie-break makes the pick deterministic on messages sharing a
        ``created_at`` (bulk imports), so the backfilled snippet matches the
        one ``update_stats`` would later derive.

        ``Blob.raw_content`` is deferred: it is the whole compressed MIME, and
        selecting it here would materialise every body of the batch at once —
        500 messages' worth, before ``--max-blob-size`` gets a chance to reject
        any of them. That is what OOM-killed this command on a deployment whose
        blobs still live in PostgreSQL. Only the blob *metadata* is joined in,
        which is what the size filter needs; the bytes are fetched later, one
        bounded chunk at a time, by ``_load_pending_content``.

        Threads with no visible message (drafts only) are dropped, as are
        oversized blobs.
        """
        latest = {
            message.thread_id: message
            for message in models.Message.objects.filter(
                thread_id__in=[thread.id for thread in batch],
                is_draft=False,
                is_trashed=False,
            )
            .order_by("thread_id", "-created_at", "-id")
            .distinct("thread_id")
            .select_related("blob")
            .defer("blob__raw_content")
        }

        todo = []
        oversized = 0
        for thread in batch:
            message = latest.get(thread.id)
            if message is None:
                continue
            if max_blob_size and message.blob and message.blob.size > max_blob_size:
                oversized += 1
                logger.info(
                    "backfill_thread_snippets: skipping oversized blob on %s (%d bytes)",
                    message.id,
                    message.blob.size,
                )
                continue
            todo.append((thread, message))
        return todo, oversized

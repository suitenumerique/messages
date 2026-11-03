"""Export of the spam corpus to object storage, for analysis and training.

Spam is about to become permanently deletable (trashbin sweep, "Empty spam"):
this keeps a copy of the messages classified as spam before they go. An
export is a snapshot: it covers the spam listed when it starts (see
``SpamExportFilters``). The export is written under ``<prefix>/`` in the
``message-exports`` storage:

- ``part-NNNNN.tar.gz.age`` (``part-NNNNN.tar.gz`` when not encrypted) holds
  ``part-NNNNN/<sha256>.eml`` files — the raw RFC 5322 bytes exactly as
  stored, never rewritten — and ``part-NNNNN/manifest.jsonl``, one JSON line
  per EML carrying what the EML itself does not: message id, flags, dates,
  delivery channel and pipeline verdicts. It deliberately leaves out what
  would tie a message to who received it (mailboxes, thread, envelope
  recipient): the corpus is about content.
- ``state.json`` records the export parameters and its progress, so that an
  interrupted run resumes where it stopped. It holds no message content and
  is never encrypted.

Parts are encrypted with age (https://age-encryption.org) to public keys
only: the content leaves the app decrypted, and neither access to the bucket
nor to the app's configuration is enough to read it back — only the holders
of the matching private keys can, with the standard ``age`` CLI. Plaintext
exports must be asked for explicitly.

EMLs are deduplicated by blob SHA-256: one spam delivered to many mailboxes
is a single EML listing every message it was delivered as, so a corpus does
not over-weight mass mailings. Blob rows are already deduplicated by hash,
but without a unique constraint; keying on the hash rather than the row also
covers concurrent duplicates. EMLs are processed in hash order, which makes
the last hash exported a stable resume cursor.

Nothing records *who* flagged a message as spam (rspamd, a rule, a webhook or
a user). ``created_at`` against ``updated_at`` in the manifest is the only
hint: a message flagged by hand after delivery was touched later.
"""

import hashlib
import io
import json
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from itertools import batched
from typing import IO, Callable, Iterator

from django.conf import settings
from django.core.files.storage import storages
from django.db.models import Count, Max, QuerySet, Sum
from django.utils import timezone

from botocore.exceptions import ClientError

from core.models import Blob, Message

EXPORT_STORAGE = "message-exports"
STATE_FILENAME = "state.json"
STATE_VERSION = 1

# ``Message.postmark`` keys worth exporting: pipeline verdicts. A whitelist, so
# that a key added later (``rcpt_to`` is a recipient address already) is left
# out until someone decides it belongs in the corpus.
EXPORTED_POSTMARK_KEYS = ("auth", "spam", "processing")


class SpamExportError(Exception):
    """The export cannot start or resume as requested."""


class SpamExportEncryptionRequiredError(SpamExportError):
    """A new export was given neither age recipients nor leave for plaintext."""


def parse_age_recipients(recipients: list[str]) -> list[str]:
    """Validate age public keys (``age1...``); return them deduplicated, sorted.

    ``pyrage`` is only imported here and when encrypting: nothing else in the
    app needs it.
    """
    from pyrage import RecipientError, x25519  # pylint: disable=import-outside-toplevel

    parsed = sorted({recipient.strip() for recipient in recipients})
    for recipient in parsed:
        try:
            x25519.Recipient.from_str(recipient)
        except RecipientError as exc:
            raise SpamExportError(
                f"Invalid age recipient {recipient!r}: {exc}"
            ) from exc
    return parsed


def _encrypt(source: IO[bytes], target: IO[bytes], recipients: list[str]) -> None:
    import pyrage  # pylint: disable=import-outside-toplevel
    from pyrage import x25519  # pylint: disable=import-outside-toplevel

    pyrage.encrypt_io(
        source, target, [x25519.Recipient.from_str(r) for r in recipients]
    )


def is_export_storage_configured() -> bool:
    """Whether the ``message-exports`` storage has a bucket to write to."""
    options = settings.STORAGES.get(EXPORT_STORAGE, {}).get("OPTIONS", {})
    return bool(options.get("bucket_name"))


@dataclass(frozen=True)
class SpamExportFilters:
    """Which spam messages an export covers.

    Messages are selected by ``created_at``, their date, as nothing records
    when a message was flagged as spam. ``until`` is pinned when the export
    starts so that a resumed run walks the same date range: spam received
    meanwhile is left to a later export. So is a message dated before
    ``until`` but flagged as spam only after its hash was listed or its part
    written: it is missed by this export and caught by a later one without
    ``since``.
    """

    until: datetime
    since: datetime | None = None
    maildomain: str | None = None

    def to_dict(self) -> dict:
        """Serialize for ``state.json``."""
        return {
            "until": self.until.isoformat(),
            "since": self.since.isoformat() if self.since else None,
            "maildomain": self.maildomain,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpamExportFilters":
        """Deserialize from ``state.json``."""
        return cls(
            until=datetime.fromisoformat(data["until"]),
            since=datetime.fromisoformat(data["since"]) if data["since"] else None,
            maildomain=data["maildomain"],
        )


def spam_messages_queryset(filters: SpamExportFilters) -> QuerySet:
    """Received messages flagged as spam that still hold their raw MIME.

    Drafts and messages we sent are left out: they are not spam someone
    received, even when they sit in a spam conversation.
    """
    queryset = Message.objects.filter(
        is_spam=True,
        is_draft=False,
        is_sender=False,
        blob__isnull=False,
        created_at__lt=filters.until,
    )
    if filters.since:
        queryset = queryset.filter(created_at__gte=filters.since)
    if filters.maildomain:
        queryset = queryset.filter(
            thread__accesses__mailbox__domain__name=filters.maildomain
        )
    # The maildomain join may repeat a message once per matching access.
    return Message.objects.filter(pk__in=queryset.values("pk")).order_by()


def compute_spam_export_stats(filters: SpamExportFilters) -> dict:
    """Size up an export without reading any blob content."""
    messages = spam_messages_queryset(filters)
    unique_blobs = (
        Blob.objects.filter(pk__in=messages.values("blob_id"))
        .values("sha256")
        .annotate(blob_size=Max("size"))
    )
    by_domain = (
        messages.values("thread__accesses__mailbox__domain__name")
        .annotate(count=Count("pk", distinct=True))
        .order_by("-count")
    )
    return {
        "messages": messages.count(),
        "emls": unique_blobs.count(),
        "bytes": unique_blobs.aggregate(total=Sum("blob_size"))["total"] or 0,
        "by_maildomain": {
            row["thread__accesses__mailbox__domain__name"]: row["count"]
            for row in by_domain
        },
    }


def list_spam_sha256s(
    filters: SpamExportFilters, after: bytes | None = None
) -> list[bytes]:
    """Distinct blob hashes of the spam to export, in ascending order.

    Listed once up front (32 bytes a hash) rather than paged: a page query
    would re-sort every remaining spam message for each part.
    """
    queryset = spam_messages_queryset(filters)
    if after is not None:
        queryset = queryset.filter(blob__sha256__gt=after)
    return [
        bytes(sha256)
        for sha256 in queryset.order_by("blob__sha256")
        .values_list("blob__sha256", flat=True)
        .distinct()
    ]


@dataclass
class SpamExportState:  # pylint: disable=too-many-instance-attributes
    """Progress of an export, persisted as ``<prefix>/state.json``."""

    filters: SpamExportFilters
    started_at: datetime
    age_recipients: list[str]
    parts: int = 0
    last_sha256: str | None = None
    emls: int = 0
    messages: int = 0
    errors: int = 0
    completed_at: datetime | None = None

    def to_json(self) -> bytes:
        """Serialize, stamping the time of the write."""
        return json.dumps(
            {
                "version": STATE_VERSION,
                "label": "spam",
                "filters": self.filters.to_dict(),
                "age_recipients": self.age_recipients,
                "started_at": self.started_at.isoformat(),
                "updated_at": timezone.now().isoformat(),
                "completed_at": (
                    self.completed_at.isoformat() if self.completed_at else None
                ),
                "parts": self.parts,
                "last_sha256": self.last_sha256,
                "emls": self.emls,
                "messages": self.messages,
                "errors": self.errors,
            },
            indent=2,
        ).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "SpamExportState":
        """Deserialize a ``state.json`` written by :meth:`to_json`."""
        data = json.loads(raw)
        if data.get("version") != STATE_VERSION:
            raise SpamExportError(f"Unsupported state version {data.get('version')!r}")
        return cls(
            filters=SpamExportFilters.from_dict(data["filters"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            age_recipients=data["age_recipients"],
            parts=data["parts"],
            last_sha256=data["last_sha256"],
            emls=data["emls"],
            messages=data["messages"],
            errors=data["errors"],
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data["completed_at"]
                else None
            ),
        )


@dataclass
class _EmlEntry:
    """One deduplicated EML of a part: its content and its messages."""

    sha256: bytes
    messages: list[Message]
    content: bytes | None = None
    error: str | None = None


def _read_entry(entry: _EmlEntry) -> _EmlEntry:
    """Load the EML of ``entry`` from the first of its blobs that reads.

    Blobs are decrypted and decompressed by ``Blob.get_content``, which only
    touches the blob storage — no database query — so it is safe to run from
    worker threads. The content is checked against its hash here, as
    ``get_content`` only does so when ``MESSAGES_BLOBS_VERIFY_HASH`` is on:
    a corrupted blob must not be exported under the name of the hash.
    """
    seen = set()
    for message in entry.messages:
        if message.blob_id in seen:
            continue
        seen.add(message.blob_id)
        try:
            content = message.blob.get_content()
            if hashlib.sha256(content).digest() != entry.sha256:
                raise ValueError(f"Blob {message.blob_id} does not match its sha256")
            entry.content = content
            entry.error = None
            return entry
        except Exception as exc:  # pylint: disable=broad-exception-caught
            entry.error = f"{type(exc).__name__}: {exc}"
    return entry


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _manifest_record(entry: _EmlEntry, path: str | None) -> dict:
    return {
        "sha256": entry.sha256.hex(),
        "path": path,
        "size": len(entry.content) if entry.content is not None else None,
        "error": entry.error,
        "messages": [
            {
                "id": str(message.id),
                "mime_id": message.mime_id,
                "created_at": _isoformat(message.created_at),
                "updated_at": _isoformat(message.updated_at),
                "sent_at": _isoformat(message.sent_at),
                "trashed_at": _isoformat(message.trashed_at),
                "is_trashed": message.is_trashed,
                "is_archived": message.is_archived,
                "thread_is_spam": message.thread.is_spam,
                "channel_type": message.channel.type if message.channel else None,
                "postmark": {
                    key: value
                    for key, value in (message.postmark or {}).items()
                    if key in EXPORTED_POSTMARK_KEYS
                },
            }
            for message in entry.messages
        ],
    }


def _add_tar_file(
    tar: tarfile.TarFile, path: str, content: bytes, mtime: datetime
) -> None:
    info = tarfile.TarInfo(path)
    info.size = len(content)
    info.mtime = int(mtime.timestamp())
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(content))


class SpamExporter:  # pylint: disable=too-many-instance-attributes
    """Write the spam corpus to ``<prefix>/`` in the export storage.

    Without ``filters``, a new export covers all spam received until it
    starts. ``age_recipients`` are the public keys a new export encrypts its
    parts to; it needs at least one, unless it is given an empty list to write
    plaintext on purpose. Rerunning on a prefix whose export did not complete
    resumes it with the filters and recipients it started with, whatever the
    recipients configured since: its parts all stay readable by the same keys.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        prefix: str,
        *,
        filters: SpamExportFilters | None = None,
        age_recipients: list[str] | None = None,
        batch_size: int = 1000,
        workers: int = 4,
        log: Callable[[str], None] = lambda message: None,
    ):
        if batch_size < 1 or workers < 1:
            raise SpamExportError("batch_size and workers must be positive")
        self.prefix = prefix.strip("/")
        self.filters = filters
        self.age_recipients = (
            None if age_recipients is None else parse_age_recipients(age_recipients)
        )
        self.batch_size = batch_size
        self.workers = workers
        self.log = log
        storage = storages[EXPORT_STORAGE]
        self.bucket = storage.bucket_name
        self.s3_client = storage.connection.meta.client

    @property
    def state_key(self) -> str:
        """Object key of the export's ``state.json``."""
        return f"{self.prefix}/{STATE_FILENAME}"

    def load_state(self) -> SpamExportState | None:
        """The persisted state of the export at this prefix, if any."""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=self.state_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return SpamExportState.from_json(response["Body"].read())

    def _save_state(self, state: SpamExportState) -> None:
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=self.state_key,
            Body=state.to_json(),
            ContentType="application/json",
        )

    def run(self) -> SpamExportState:
        """Export (or resume exporting) and return the final state."""
        state = self.load_state()
        if state is None:
            if self.age_recipients is None:
                raise SpamExportEncryptionRequiredError(
                    "A new export must be encrypted to at least one age "
                    "recipient, or explicitly allowed to be plaintext."
                )
            state = SpamExportState(
                filters=self.filters or SpamExportFilters(until=timezone.now()),
                started_at=timezone.now(),
                age_recipients=self.age_recipients,
            )
            self._save_state(state)
        elif self.filters is not None and self.filters != state.filters:
            raise SpamExportError(
                f"Another export, with other filters, is at {self.prefix}/: "
                f"{state.filters.to_dict()}"
            )
        elif state.completed_at:
            self.log(f"Export at {self.prefix}/ already completed.")
            return state
        else:
            if (
                self.age_recipients is not None
                and self.age_recipients != state.age_recipients
            ):
                self.log(
                    "The configured age recipients changed since this export "
                    "started; it keeps encrypting to its original ones: "
                    f"{state.age_recipients or 'none (plaintext)'}."
                )
            self.log(
                f"Resuming export at {self.prefix}/ after part {state.parts} "
                f"({state.emls} EMLs exported)."
            )

        after = bytes.fromhex(state.last_sha256) if state.last_sha256 else None
        sha256s = list_spam_sha256s(state.filters, after=after)
        self.log(f"{len(sha256s)} EMLs to export.")

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            for chunk in batched(sha256s, self.batch_size, strict=False):
                part_index = state.parts + 1
                emls, messages, errors = self._export_part(
                    executor, state, part_index, chunk
                )
                state.parts = part_index
                state.last_sha256 = chunk[-1].hex()
                state.emls += emls
                state.messages += messages
                state.errors += errors
                self._save_state(state)
                self.log(
                    f"part {part_index:05d}: {emls} EMLs, {messages} messages, "
                    f"{errors} errors (total {state.emls} EMLs)"
                )

        state.completed_at = timezone.now()
        self._save_state(state)
        return state

    def _window_entries(
        self, filters: SpamExportFilters, sha256s: tuple[bytes, ...]
    ) -> list[_EmlEntry]:
        """Group the spam messages of ``sha256s`` into one entry per hash.

        Each blob is fetched once, however many messages it was delivered as:
        joining it to every message row would hold one copy of its content per
        message. Hashes whose messages no longer qualify (purged, or unflagged
        since the hashes were listed) simply yield no entry.
        """
        messages = list(
            spam_messages_queryset(filters)
            .filter(blob__sha256__in=sha256s)
            .select_related("channel", "thread")
            .order_by("created_at", "id")
        )
        blobs = Blob.objects.in_bulk({message.blob_id for message in messages})
        entries: dict[bytes, _EmlEntry] = {}
        for message in messages:
            blob = blobs.get(message.blob_id)
            if blob is None:  # Garbage-collected since the messages were read.
                continue
            message.blob = blob
            sha256 = bytes(blob.sha256)
            entries.setdefault(sha256, _EmlEntry(sha256=sha256, messages=[]))
            entries[sha256].messages.append(message)
        return [entries[sha256] for sha256 in sha256s if sha256 in entries]

    def _iter_entries(
        self,
        executor: ThreadPoolExecutor,
        filters: SpamExportFilters,
        sha256s: tuple[bytes, ...],
    ) -> Iterator[_EmlEntry]:
        """Yield the entries of ``sha256s`` in hash order, read concurrently.

        Rows and contents are fetched one window of hashes at a time — the
        window the workers read — so memory is bounded by that window, not by
        the part size nor by how many mailboxes a spam was delivered to.
        """
        for window in batched(sha256s, self.workers * 4, strict=False):
            yield from executor.map(_read_entry, self._window_entries(filters, window))

    def _export_part(
        self,
        executor: ThreadPoolExecutor,
        state: SpamExportState,
        part_index: int,
        sha256s: tuple[bytes, ...],
    ) -> tuple[int, int, int]:
        """Write and upload one part; return (emls, messages, errors).

        The archive goes through temporary files rather than memory: a part
        can weigh hundreds of MB, and one-off containers are small. The object
        only appears once fully uploaded, so an interrupted part leaves
        nothing behind for the resume to trip on.
        """
        part_name = f"part-{part_index:05d}"
        key = f"{self.prefix}/{part_name}.tar.gz"
        with tempfile.TemporaryFile() as archive:
            counts = self._write_archive(
                archive, executor, state.filters, part_name, sha256s
            )
            archive.seek(0)
            if not state.age_recipients:
                self._upload(archive, key, "application/gzip")
                return counts
            with tempfile.TemporaryFile() as encrypted:
                _encrypt(archive, encrypted, state.age_recipients)
                encrypted.seek(0)
                self._upload(encrypted, f"{key}.age", "application/octet-stream")
        return counts

    def _upload(self, fileobj: IO[bytes], key: str, content_type: str) -> None:
        self.s3_client.upload_fileobj(
            fileobj, self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def _write_archive(  # pylint: disable=too-many-arguments
        self,
        archive: IO[bytes],
        executor: ThreadPoolExecutor,
        filters: SpamExportFilters,
        part_name: str,
        sha256s: tuple[bytes, ...],
    ) -> tuple[int, int, int]:
        """Write the ``tar.gz`` of a part into ``archive``."""
        manifest = io.BytesIO()
        emls = messages = errors = 0
        with tarfile.open(
            fileobj=archive, mode="w:gz", format=tarfile.PAX_FORMAT
        ) as tar:
            for entry in self._iter_entries(executor, filters, sha256s):
                path = None
                if entry.content is None:
                    errors += 1
                else:
                    path = f"{part_name}/{entry.sha256.hex()}.eml"
                    _add_tar_file(
                        tar, path, entry.content, entry.messages[0].created_at
                    )
                    emls += 1
                messages += len(entry.messages)
                manifest.write(
                    json.dumps(_manifest_record(entry, path)).encode() + b"\n"
                )
            _add_tar_file(
                tar, f"{part_name}/manifest.jsonl", manifest.getvalue(), timezone.now()
            )
        return emls, messages, errors

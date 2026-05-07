"""Read-only audit of blob storage consistency.

Compares the ``Blob`` rows in PostgreSQL against the object storage
bucket and reports drift. Never mutates state — anything found is
surfaced for the operator to act on (re-upload, run
``re_store_blobs``, ``aws s3 rm``, etc.).

Two complementary directions:

- ``--mode=db-to-storage``: for every row marked OBJECT_STORAGE,
  HEAD the expected S3 object. Reports ``MISSING`` for any drift.
- ``--mode=storage-to-db``: LIST every object under ``blobs/`` and
  look up the matching DB row. Reports ``ORPHAN`` for any S3
  object not referenced by any row, and ``INVALID PATH`` for keys
  that don't match the ``blobs/{key_id}/{sha[:3]}/{sha}`` shape.
- ``--mode=full`` (default): both, back-to-back.

``--verify-hashes`` extends ``storage-to-db`` to download each
object, decrypt + decompress, and re-check the SHA-256 against
the row. Slow; use after a storage incident or on a sampling
schedule.

``--limit`` and ``--start-after-key`` let large buckets be
checked in chunks across runs (S3 lists in lexicographic key
order; ``StartAfter`` resumes after the last key seen).
"""

import hashlib
from typing import Iterator

from django.core.management.base import BaseCommand, CommandError

import pyzstd

from core.enums import BlobStorageLocationChoices, CompressionTypeChoices
from core.models import Blob
from core.services.tiered_storage import TieredStorageService


class Command(BaseCommand):
    """Read-only audit of blob storage consistency."""

    help = "Audit blob storage consistency (read-only)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.service: TieredStorageService | None = None
        self.verify_hashes: bool = False
        self.limit: int = 0
        self.start_after_key: str = ""

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["db-to-storage", "storage-to-db", "full"],
            default="full",
            help="Audit mode: db-to-storage (check DB rows have storage backing), "
            "storage-to-db (find orphan storage objects), or full (both).",
        )
        parser.add_argument(
            "--verify-hashes",
            action="store_true",
            help="Re-download and verify SHA-256 hashes against DB rows. "
            "Slow; only effective in storage-to-db / full modes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of items checked per direction (0=unlimited).",
        )
        parser.add_argument(
            "--start-after-key",
            default="",
            help="Resume listing storage objects after this key (S3 lexicographic order). "
            "Pair with --limit to chunk a multi-million-object bucket across runs.",
        )

    def handle(self, *args, **options):
        self.service = TieredStorageService()
        self.verify_hashes = options["verify_hashes"]
        self.limit = options["limit"]
        self.start_after_key = options["start_after_key"]

        if not self.service.enabled:
            self.stderr.write(
                self.style.ERROR(
                    "Object storage not configured. Configure message-blobs in STORAGES to enable."
                )
            )
            return

        mode = options["mode"]

        if mode in ("db-to-storage", "full"):
            self._verify_db_to_storage()
        if mode in ("storage-to-db", "full"):
            self._verify_storage_to_db()

    def _verify_db_to_storage(self):
        """Iterate blob DB rows -> verify all expected objects exist in storage."""
        self.stdout.write(
            self.style.MIGRATE_HEADING("\n=== DB to Storage Verification ===")
        )
        self.stdout.write(
            "Checking that all blobs marked as OBJECT_STORAGE exist in storage..."
        )

        missing_count = 0
        checked_count = 0

        queryset = Blob.objects.filter(
            storage_location=BlobStorageLocationChoices.OBJECT_STORAGE
        )

        if self.limit > 0:
            queryset = queryset[: self.limit]

        for blob in queryset.iterator(chunk_size=1000):
            checked_count += 1
            key = self.service.compute_storage_key_for_blob(blob)

            if not self.service.storage.exists(key):
                missing_count += 1
                # stderr — data may be lost, manual intervention required
                self.stderr.write(
                    self.style.ERROR(
                        f"MISSING: Blob {blob.id} -> {key} "
                        f"(sha256: {blob.sha256.hex()}, size: {blob.size} bytes)"
                    )
                )

            if checked_count % 1000 == 0:
                self.stdout.write(f"  Checked {checked_count} blobs...")

        self.stdout.write("")
        self.stdout.write(f"Checked: {checked_count} blobs")
        if missing_count == 0:
            self.stdout.write(
                self.style.SUCCESS("Result: All blobs have storage backing")
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"Result: {missing_count} blobs missing from storage")
            )

    def _verify_storage_to_db(self):
        """Iterate storage objects -> find orphans and optionally verify hashes."""
        self.stdout.write(
            self.style.MIGRATE_HEADING("\n=== Storage to DB Verification ===")
        )
        self.stdout.write("Checking storage objects for orphans and integrity...")

        orphan_count = 0
        invalid_count = 0
        hash_mismatch_count = 0
        checked_count = 0

        try:
            objects = self._list_storage_objects("blobs/")
        except Exception as e:  # pylint: disable=broad-except
            raise CommandError(f"Failed to list storage objects: {e}") from e

        for obj_name in objects:
            if self.limit > 0 and checked_count >= self.limit:
                break

            checked_count += 1

            # Path format: blobs/{key_id}/{sha[:3]}/{sha}
            parts = obj_name.split("/")
            try:
                if len(parts) != 4 or parts[0] != "blobs":
                    raise ValueError("unexpected path shape")
                if len(parts[3]) != 64 or parts[2] != parts[3][:3]:
                    raise ValueError("shard prefix or sha length mismatch")
                key_id = int(parts[1])
                sha_bytes = bytes.fromhex(parts[3])
            except ValueError:
                invalid_count += 1
                self.stdout.write(self.style.WARNING(f"INVALID PATH: {obj_name}"))
                continue

            # Compression isn't in the path — pull it (and ref existence)
            # from the DB row in one query.
            ref_compression = (
                Blob.objects.filter(
                    sha256=sha_bytes,
                    storage_location=BlobStorageLocationChoices.OBJECT_STORAGE,
                    encryption_key_id=key_id,
                )
                .values_list("compression", flat=True)
                .first()
            )
            refs_exist = ref_compression is not None

            if not refs_exist:
                orphan_count += 1
                self.stdout.write(self.style.WARNING(f"ORPHAN: {obj_name}"))

            if self.verify_hashes and refs_exist:
                if not self._verify_blob_hash(
                    obj_name, sha_bytes, key_id, ref_compression
                ):
                    hash_mismatch_count += 1

            if checked_count % 100 == 0:
                self.stdout.write(f"  Checked {checked_count} objects...")

        self.stdout.write("")
        self.stdout.write(f"Checked: {checked_count} storage objects")
        self.stdout.write(f"Orphans: {orphan_count}")
        self.stdout.write(f"Invalid paths: {invalid_count}")
        if self.verify_hashes:
            self.stdout.write(f"Hash mismatches: {hash_mismatch_count}")

        if orphan_count == 0 and invalid_count == 0 and hash_mismatch_count == 0:
            self.stdout.write(self.style.SUCCESS("Result: Storage is consistent"))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Result: Found {orphan_count + invalid_count + hash_mismatch_count} issues"
                )
            )

    def _list_storage_objects(self, prefix: str) -> Iterator[str]:
        """List object keys under a prefix using boto3 pagination.

        Honors ``--start-after-key`` so a multi-million-object bucket
        can be checked in chunks; pair with ``--limit`` to cap each
        chunk's wall time.
        """
        bucket = self.service.storage.bucket
        paginator = bucket.meta.client.get_paginator("list_objects_v2")
        kwargs = {"Bucket": bucket.name, "Prefix": prefix}
        if self.start_after_key:
            kwargs["StartAfter"] = self.start_after_key
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def _verify_blob_hash(
        self,
        obj_name: str,
        expected_sha_bytes: bytes,
        key_id: int,
        compression: int,
    ) -> bool:
        """Download, decrypt, decompress, and check the SHA-256 against
        ``expected_sha_bytes`` (the value the DB row claims)."""
        try:
            with self.service.storage.open(obj_name, "rb") as f:
                encrypted = f.read()
            decrypted = self.service.decrypt(encrypted, key_id, expected_sha_bytes)

            if compression == CompressionTypeChoices.ZSTD:
                original = pyzstd.decompress(decrypted)
            else:
                original = decrypted

            actual_hash = hashlib.sha256(original).digest()
            if actual_hash != expected_sha_bytes:
                self.stdout.write(
                    self.style.ERROR(
                        f"HASH MISMATCH: {obj_name}\n"
                        f"  Expected: {expected_sha_bytes.hex()}\n"
                        f"  Actual:   {actual_hash.hex()}"
                    )
                )
                return False
            return True
        except Exception as e:  # pylint: disable=broad-except
            self.stdout.write(self.style.ERROR(f"VERIFY ERROR: {obj_name} - {e}"))
            return False

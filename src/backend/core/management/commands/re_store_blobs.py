"""Reconcile every blob with the active configuration.

"Re-store" = read each blob, decrypt under its current key,
re-encrypt under the active key, and write the result to the
target storage location implied by ``MESSAGES_BLOBS_OFFLOAD_ENABLED``.

Two operations, applied based on settings:

- **Key rotation** (always): every row whose ``encryption_key_id``
  differs from the entry currently flagged ``active=true`` in
  ``MESSAGES_BLOBS_ENCRYPT_KEYS`` is re-encrypted under the active
  key. The path on disk is ``blobs/{key_id}/...`` so rotated
  blobs land at a new path; the old path is dropped on commit.

- **Restore** (only when ``MESSAGES_BLOBS_OFFLOAD_ENABLED`` is
  False): every ``OBJECT_STORAGE`` row is pulled back into
  PostgreSQL — ciphertext is downloaded, decrypted under its
  original key, re-encrypted under the active key, and written
  to ``raw_content``. The S3 object is dropped on commit once
  no rows reference it.

The PG → S3 direction (offload) is owned by
``offload_blobs_task`` (periodic celery task with age/size
filters) — not exposed here, on purpose: this command does
"reconcile to current config", offload does "move cold blobs
out of PG".

``--dry-run`` prints intent without writing. ``--limit`` caps
the worklist; rerun until empty.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from core.enums import BlobStorageLocationChoices
from core.models import Blob
from core.services.tiered_storage import TieredStorageService, sha256_advisory_lock


class Command(BaseCommand):
    """Reconcile blobs with the active configuration (key rotation + optional PG restore)."""

    help = "Reconcile blobs with the active configuration"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.service: TieredStorageService | None = None
        self.dry_run: bool = False
        self.limit: int = 0

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of blobs reconciled (0=unlimited). Rerun until empty.",
        )

    def handle(self, *args, **options):
        self.service = TieredStorageService()
        self.dry_run = options["dry_run"]
        self.limit = options["limit"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "\n=== Re-store (rotation + optional restore) ==="
            )
        )

        target_key_id = self.service.active_key_id
        offload_enabled = bool(settings.MESSAGES_BLOBS_OFFLOAD_ENABLED)
        restore_to_pg = not offload_enabled

        self.stdout.write(f"Target encryption key_id: {target_key_id}")
        if self.service.encryption_keys:
            self.stdout.write(
                f"Available keys: {list(self.service.encryption_keys.keys())}"
            )

        # Loud hint when the active config maps to "no encryption"
        # AND there are encrypted blobs in the DB. Same state an
        # operator sees if they removed ``active: true`` while
        # planning a rotation; running this command then would
        # decrypt everything in place. Surfacing the count gives
        # them a chance to abort before any writes land.
        if target_key_id == 0:
            encrypted_count = Blob.objects.exclude(encryption_key_id=0).count()
            if encrypted_count:
                self.stdout.write(
                    self.style.WARNING(
                        f"No key has active=true: {encrypted_count} encrypted "
                        "blob(s) will be DECRYPTED in place. If this isn't "
                        "what you meant, abort now and set active=true on "
                        "the target key first."
                    )
                )

        if restore_to_pg:
            self.stdout.write(
                self.style.WARNING(
                    "MESSAGES_BLOBS_OFFLOAD_ENABLED is False — "
                    "OBJECT_STORAGE blobs will be restored to PostgreSQL."
                )
            )
            if not self.service.enabled:
                # Need bucket access to read ciphertext on the way home.
                self.stderr.write(
                    self.style.ERROR(
                        "Object storage is not configured but OBJECT_STORAGE "
                        "rows exist. Configure STORAGE_MESSAGE_BLOBS_* to "
                        "let restore download them, or accept that those "
                        "blobs cannot be reached."
                    )
                )
                return

        # Active key must be in the dict if it's non-zero (passthrough at 0).
        if target_key_id > 0 and str(target_key_id) not in self.service.encryption_keys:
            self.stderr.write(
                self.style.ERROR(
                    f"Active key_id {target_key_id} not found in MESSAGES_BLOBS_ENCRYPT_KEYS. "
                    f"Available keys: {list(self.service.encryption_keys.keys())}"
                )
            )
            return

        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        worklist = self._build_worklist(target_key_id, restore_to_pg)
        total_count = len(worklist)
        self.stdout.write(f"Work units to re-store: {total_count}")

        if total_count == 0:
            self.stdout.write(
                self.style.SUCCESS("Nothing to do — all blobs already match config")
            )
            return

        rotated_count = 0
        restored_count = 0
        error_count = 0
        skipped_count = 0

        for blob_id in worklist:
            try:
                blob = Blob.objects.get(id=blob_id)
            except Blob.DoesNotExist:
                # Concurrently deleted between worklist build and processing.
                skipped_count += 1
                continue
            try:
                op = self._process_one(blob, target_key_id, restore_to_pg)
                if op == "rotated":
                    rotated_count += 1
                elif op == "restored":
                    restored_count += 1
                else:
                    skipped_count += 1
            except Exception as e:  # pylint: disable=broad-except
                error_count += 1
                self.stderr.write(
                    self.style.ERROR(f"ERROR re-storing blob {blob.id}: {e}")
                )

            done = rotated_count + restored_count + error_count + skipped_count
            if done % 100 == 0:
                self.stdout.write(f"  Progress: {done}/{total_count}")

        self.stdout.write("")
        self.stdout.write(f"Re-encrypted (rotation): {rotated_count}")
        self.stdout.write(f"Restored to PostgreSQL: {restored_count}")
        self.stdout.write(f"Skipped: {skipped_count}")
        self.stdout.write(f"Errors: {error_count}")

        if error_count == 0:
            self.stdout.write(self.style.SUCCESS("Re-store completed successfully"))
        else:
            self.stdout.write(
                self.style.WARNING(f"Re-store completed with {error_count} errors")
            )

    def _build_worklist(self, target_key_id: int, restore_to_pg: bool) -> list:
        """Build the list of Blob ids to reconcile.

        - POSTGRES rows: every row not at the active key needs row-level
          rotation. One unit per row.
        - OBJECT_STORAGE rows:
            * Restore mode: every row needs to be pulled back to PG with
              its own raw_content, so each row is a unit.
            * Rotation only: rows sharing (sha256, encryption_key_id)
              share one stored object and rotate as a cohort — one rep
              per cohort suffices.

        ``--limit`` applies to the resulting worklist so "N units" means
        N actual operations regardless of cohort sizes.

        Streams ids via ``.iterator()`` and stops as soon as ``--limit``
        is hit, so a multi-million-row table never lands in worker RAM.
        """
        postgres_qs = (
            Blob.objects.filter(
                storage_location=BlobStorageLocationChoices.POSTGRES,
            )
            .exclude(encryption_key_id=target_key_id)
            .values_list("id", flat=True)
        )

        if restore_to_pg:
            object_storage_qs = Blob.objects.filter(
                storage_location=BlobStorageLocationChoices.OBJECT_STORAGE
            ).values_list("id", flat=True)
        else:
            object_storage_qs = (
                Blob.objects.filter(
                    storage_location=BlobStorageLocationChoices.OBJECT_STORAGE,
                )
                .exclude(encryption_key_id=target_key_id)
                .order_by("sha256", "encryption_key_id", "id")
                .distinct("sha256", "encryption_key_id")
                .values_list("id", flat=True)
            )

        worklist: list = []
        cap = self.limit if self.limit > 0 else None
        for qs in (postgres_qs, object_storage_qs):
            if cap is not None and len(worklist) >= cap:
                break
            for blob_id in qs.iterator(chunk_size=1000):
                worklist.append(blob_id)
                if cap is not None and len(worklist) >= cap:
                    break
        return worklist

    def _process_one(self, blob: Blob, target_key_id: int, restore_to_pg: bool) -> str:
        """Process one blob under transaction + per-sha advisory lock.

        Returns one of: ``"rotated"``, ``"restored"``, ``"skipped"``.
        """
        sha256 = bytes(blob.sha256)
        will_restore = (
            restore_to_pg
            and blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
        )

        if self.dry_run:
            verb = "restore to PG" if will_restore else "re-encrypt"
            self.stdout.write(
                f"  Would {verb} blob {blob.id} "
                f"({blob.get_storage_location_display()}): "
                f"key_id {blob.encryption_key_id} -> {target_key_id}"
            )
            return "restored" if will_restore else "rotated"

        with transaction.atomic(), sha256_advisory_lock(sha256):
            blob.refresh_from_db()
            # Re-check storage_location after refresh — a concurrent
            # actor could have changed it.
            will_restore = (
                restore_to_pg
                and blob.storage_location == BlobStorageLocationChoices.OBJECT_STORAGE
            )
            if will_restore:
                done = self.service.re_store_blob_in_database(blob, target_key_id)
                op = "restored" if done else "skipped"
            elif blob.encryption_key_id == target_key_id:
                # Already at the active key — the worklist was built
                # before we took the lock, and another run (or a
                # concurrent re_store_blobs) may have rotated this
                # row already. Skip explicitly so the contract is
                # local to _process_one rather than relying on
                # rotate_blob's internal short-circuit.
                op = "skipped"
            else:
                done = self.service.rotate_blob(blob, target_key_id)
                op = "rotated" if done else "skipped"

        if op != "skipped":
            verb = "Restored" if op == "restored" else "Re-encrypted"
            self.stdout.write(f"  {verb} blob {blob.id} -> key_id {target_key_id}")
        return op

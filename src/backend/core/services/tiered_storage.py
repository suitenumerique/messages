"""
Tiered Storage Service for blob offloading to object storage.

This service handles:
- Uploading blobs to object storage (S3-compatible)
- Downloading blobs from object storage
- Encryption/decryption using AES-256-GCM
- Deduplication via SHA256-based storage keys
"""

import hashlib
import os
from contextlib import contextmanager
from logging import getLogger
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import connection, transaction

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.enums import BlobStorageLocationChoices

# AEAD nonce size — 12 bytes is the standard for both AES-GCM and
# ChaCha20-Poly1305 (RFC 7539); both ``cryptography`` AEAD primitives
# share this layout. Stored ciphertext: ``nonce(12) || ct+tag(16)``.
_NONCE_SIZE = 12

# Minimum length for a configured key string. SHA-256 spreads bits
# uniformly across the 32-byte output, but it can't add entropy that
# wasn't there — operators must supply something high-entropy.
# 32 chars chosen as a sane floor (the system check warns below this).
MIN_KEY_LEN = 32

# AEAD algorithm identifiers. Each value is a complete spec — cipher,
# nonce size, tag size, and the AAD policy (currently: AAD = blob.sha256
# binds ciphertext to its content cohort, so a swap of bytes between two
# blob paths fails the auth tag). A new spec needs a new identifier;
# never repurpose an existing one.
ALGO_AES_GCM = "aes-gcm"
_KNOWN_ALGOS = frozenset({ALGO_AES_GCM})


def decode_key(secret: str) -> bytes:
    """Derive a 32-byte AEAD key from an arbitrary operator-supplied secret.

    We accept any string (passphrase, base64, hex, …) and SHA-256 it to a
    fixed 32-byte key. The hash provides domain separation and uniform
    distribution; it does NOT add entropy. Operators are responsible for
    supplying a high-entropy value (e.g. ``openssl rand -base64 32``).
    """
    if not isinstance(secret, str) or not secret:
        raise ValueError("encryption key must be a non-empty string")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def normalize_key_entry(entry):
    """Validate an operator-configured key entry.

    Accepted shape:
        ``{"algo": "<name>", "secret": "<secret>", "active": <bool>}``

    ``algo`` and ``secret`` are required and must always be explicit (no
    shorthand) so switching algos later doesn't re-bind any keys silently.
    ``active`` is optional (defaults to False); set it to True on exactly
    one entry to make that key the one new blobs are encrypted with.
    Inactive entries stay readable for legacy ciphertext.

    Raises ``ValueError`` on bad shapes, unknown algos, or non-bool
    ``active``.
    """
    if not isinstance(entry, dict) or "algo" not in entry or "secret" not in entry:
        raise ValueError(
            "encryption key entry must be "
            '{"algo": "...", "secret": "...", "active": <bool>} dict'
        )
    algo = entry["algo"]
    if algo not in _KNOWN_ALGOS:
        raise ValueError(
            f"unknown encryption algo {algo!r}; supported: {sorted(_KNOWN_ALGOS)}"
        )
    active = entry.get("active", False)
    if not isinstance(active, bool):
        raise ValueError(f"'active' must be a bool, got {type(active).__name__}")
    return {"algo": algo, "secret": entry["secret"], "active": active}


def find_active_key_id(encryption_keys: dict) -> int:
    """Return the key_id of the entry flagged ``active=true``, else 0.

    Returns 0 if no entry is marked active or more than one is — the
    system check will surface the >1 case as an error at boot.
    Silently coerces (returns 0) outside that path so callers don't
    need to repeat the system-check logic at runtime.
    """
    active_ids = []
    for key_id_str, entry in encryption_keys.items():
        if isinstance(entry, dict) and entry.get("active") is True:
            try:
                active_ids.append(int(key_id_str))
            except (TypeError, ValueError):
                pass
    if len(active_ids) > 1:
        # The system check (core.E003) catches this at boot, but if the
        # check was skipped or the dict was mutated at runtime (override_settings,
        # live reconfig), we'd silently fall back to passthrough — log loud.
        logger.error(
            "MESSAGES_BLOBS_ENCRYPT_KEYS has %d entries flagged active=true "
            "(key_ids: %s); coercing to 0 (encryption disabled). Fix the config.",
            len(active_ids),
            sorted(active_ids),
        )
    return active_ids[0] if len(active_ids) == 1 else 0


def _build_aead(algo: str, key_bytes: bytes):
    """Construct the AEAD primitive for an algo identifier.

    Add a branch here when introducing a new algo; ``cryptography``'s
    AEAD classes share an ``encrypt(nonce, data, aad) / decrypt(...)``
    interface so callers don't need to know which one they got.
    """
    if algo == ALGO_AES_GCM:
        return AESGCM(key_bytes)
    raise ValueError(f"unknown encryption algo {algo!r}")


if TYPE_CHECKING:
    from core.models import Blob

logger = getLogger(__name__)


# Advisory-lock namespace. Postgres has a single global keyspace for
# ``pg_advisory_xact_lock(bigint)``; using the two-arg
# ``(classid, objid)`` form reserves a 32-bit ``classid`` for blob-cohort
# locks so any future advisory-lock user (third-party app, new feature)
# can pick a different ``classid`` and never collide.
_ADVISORY_LOCK_CLASSID_BLOB = 0x626C6F62  # 'blob' in ASCII


@contextmanager
def sha256_advisory_lock(sha256_bytes: bytes, *, blocking: bool = True):
    """Hold a Postgres advisory lock keyed on a blob's sha256 cohort.

    Must be called inside ``transaction.atomic()`` — the lock is bound to
    the current transaction and released on commit/rollback. Acts as a
    cluster-wide mutex for all offload, dedup, cleanup, and re-encrypt
    work that touches blobs sharing this content.

    With ``blocking=True`` waits until the lock is granted.
    With ``blocking=False`` yields ``True`` if acquired, ``False`` if held
    elsewhere — caller is expected to retry.
    """
    # First 4 bytes of sha256 as a signed 32-bit int — the two-arg
    # ``pg_advisory_xact_lock(classid, objid)`` form takes two int4s.
    # Collisions within the blob namespace are 2^-32, large enough to
    # be exceedingly rare in practice; combined with the per-cohort
    # work being short, an occasional false-share is operationally
    # invisible.
    objid = int.from_bytes(sha256_bytes[:4], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        if blocking:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                [_ADVISORY_LOCK_CLASSID_BLOB, objid],
            )
            yield True
        else:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)",
                [_ADVISORY_LOCK_CLASSID_BLOB, objid],
            )
            yield cursor.fetchone()[0]


class TieredStorageService:
    """Service for tiered blob storage operations using object storage backend."""

    def __init__(self):
        """Initialize the service, checking if object storage is configured.

        ``message-blobs`` is always present in ``STORAGES`` because the
        Configuration class can't conditionally drop entries; we infer
        "actually configured" from the resolved options instead. An
        endpoint_url indicates a custom S3-compatible object storage
        backend; an access_key indicates explicit credentials (AWS).
        IAM-role-only setups need to set a dummy access_key to opt in.
        """
        self._storage = None
        opts = settings.STORAGES.get("message-blobs", {}).get("OPTIONS", {})
        self.enabled = bool(opts.get("endpoint_url") or opts.get("access_key"))
        # encryption_keys: dict of key_id -> {"algo", "secret", "active"}.
        # active_key_id: derived from whichever entry has active=true (0
        # if none — encryption disabled).
        self.encryption_keys = settings.MESSAGES_BLOBS_ENCRYPT_KEYS or {}
        self.active_key_id = find_active_key_id(self.encryption_keys)

    @property
    def storage(self):
        """Lazy load the storage backend."""
        if self._storage is None and self.enabled:
            self._storage = storages["message-blobs"]
        return self._storage

    @staticmethod
    def compute_storage_key(sha256_bytes: bytes, key_id: int) -> str:
        """Compute the storage path for a blob's ciphertext.

        Format: ``blobs/{key_id}/{sha[:3]}/{sha}``.

        - ``key_id`` first: lets blobs encrypted with different keys
          coexist on disk (essential for crash-safe online rotation),
          and lets ops list everything at a key with one prefix scan.
        - 3-char sha prefix: 4,096 sub-shards for S3 request-rate balance.

        Compression is intentionally NOT in the path. A given sha256
        adopts whatever compression the first stored copy used and
        sticks with it forever (later uploads that requested a
        different algorithm just inherit, see ``upload_blob``). One
        sha256 → one stored object — full stop.
        """
        sha_hex = sha256_bytes.hex()
        return f"blobs/{key_id}/{sha_hex[:3]}/{sha_hex}"

    @classmethod
    def compute_storage_key_for_blob(cls, blob: "Blob") -> str:
        """Convenience wrapper around ``compute_storage_key`` for a Blob row."""
        return cls.compute_storage_key(bytes(blob.sha256), blob.encryption_key_id)

    def _aead(self, key_id: int):
        """Build the AEAD primitive for ``key_id`` (raises if missing/unknown)."""
        key_id_str = str(key_id)
        if key_id_str not in self.encryption_keys:
            raise ValueError(
                f"Encryption key_id {key_id} not found in MESSAGES_BLOBS_ENCRYPT_KEYS"
            )
        entry = normalize_key_entry(self.encryption_keys[key_id_str])
        return _build_aead(entry["algo"], decode_key(entry["secret"]))

    def encrypt(self, data: bytes, sha256: bytes) -> tuple[bytes, int]:
        """Encrypt under the active key, binding ``sha256`` as AAD.

        Returns ``(nonce(12) || ciphertext+tag(16), key_id)``. ``key_id=0``
        means encryption is disabled and the bytes pass through unchanged.

        ``sha256`` must be the 32-byte digest of the (uncompressed) blob
        content. Binding it as AAD makes the ciphertext non-portable
        across blobs: copying these bytes onto a different blob's storage
        path causes ``decrypt`` to fail with ``InvalidTag``.
        """
        if not self.encryption_keys or self.active_key_id == 0:
            return data, 0
        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = self._aead(self.active_key_id).encrypt(nonce, data, sha256)
        return nonce + ciphertext, self.active_key_id

    def decrypt(self, data: bytes, key_id: int, sha256: bytes) -> bytes:
        """Decrypt a token produced by ``encrypt``, verifying ``sha256`` as AAD.

        ``key_id=0`` is the passthrough sentinel. Raises ``ValueError`` if
        the key is unknown, ``InvalidTag`` if the ciphertext or tag is
        corrupted, decrypted with the wrong key, or paired with a
        different ``sha256`` than was supplied at encrypt time.
        """
        if key_id == 0:
            return data
        nonce, ciphertext = data[:_NONCE_SIZE], data[_NONCE_SIZE:]
        return self._aead(key_id).decrypt(nonce, ciphertext, sha256)

    def get_existing_sibling(self, sha256_bytes: bytes) -> "tuple[int, int] | None":
        """Return ``(encryption_key_id, compression)`` of any OBJECT_STORAGE
        sibling for this sha256, or ``None`` if none exists.

        A sha256 has exactly one stored object across the cluster. The
        sibling's ``compression`` is what was used to produce those
        stored bytes — new uploads that find a sibling adopt both its
        ``key_id`` (so they read from the same path) and its
        ``compression`` (so ``restore()`` decompresses correctly).
        """
        # pylint: disable-next=import-outside-toplevel
        from core.models import Blob

        existing = (
            Blob.objects.filter(
                sha256=sha256_bytes,
                storage_location=BlobStorageLocationChoices.OBJECT_STORAGE,
            )
            .values("encryption_key_id", "compression")
            .first()
        )
        if existing is None:
            return None
        return existing["encryption_key_id"], existing["compression"]

    def upload_blob(self, blob: "Blob") -> "tuple[int, int]":
        """Upload a blob's already-encrypted raw_content to object storage.

        Returns ``(encryption_key_id, compression)`` — the values the
        caller must persist on the new blob row before flipping its
        ``storage_location`` to OBJECT_STORAGE. On a dedup hit these
        come from the existing sibling, not from the new blob: a given
        sha256 keeps the encryption key and compression algorithm of
        whichever copy hit the bucket first, regardless of what the
        configured defaults are at upload time.

        We trust the DB: if a sibling row exists, we dedup unconditionally.
        Drift between DB and S3 (external deletion, lifecycle expiry, etc.)
        is detected offline by ``verify_blobs --mode=db-to-storage``;
        we don't pay an S3 HEAD per dedup hit to guard the cluster against
        a misuse that's already an operator-fix scenario.
        """
        if not self.enabled:
            raise RuntimeError("Object storage is not configured")
        if blob.raw_content is None:
            raise ValueError(f"Blob {blob.id} has no raw_content to upload")

        sha256_bytes = bytes(blob.sha256)
        sibling = self.get_existing_sibling(sha256_bytes)
        if sibling is not None:
            existing_key_id, existing_compression = sibling
            logger.debug(
                "Blob %s deduped against existing sibling at key_id=%d",
                blob.id,
                existing_key_id,
            )
            return existing_key_id, existing_compression

        key = self.compute_storage_key_for_blob(blob)
        self.storage.save(key, ContentFile(blob.raw_content))
        logger.info("Uploaded blob %s to object storage", blob.id)
        return blob.encryption_key_id, blob.compression

    def download_blob(self, blob: "Blob") -> bytes:
        """Download and decrypt a blob's content. Returns compressed bytes."""
        if not self.enabled:
            raise RuntimeError("Object storage is not configured")

        key = self.compute_storage_key_for_blob(blob)
        with self.storage.open(key, "rb") as f:
            encrypted = f.read()
        return self.decrypt(encrypted, blob.encryption_key_id, bytes(blob.sha256))

    def rotate_blob(self, blob: "Blob", target_key_id: int) -> bool:
        """Re-encrypt a blob (and its OBJECT_STORAGE cohort) with target_key_id.

        ``target_key_id`` MUST equal ``self.active_key_id`` — ``encrypt()``
        only knows how to encrypt with the active key, so rotating to any
        other key would produce ciphertext under one key while the DB row
        records another.

        Returns True if rotated, False if already at target / unrotatable.

        For OBJECT_STORAGE the rotation is a 3-step sequence — each step
        independently atomic, so a crash at any boundary leaves the blob
        readable from one consistent path:

        1. write new ciphertext at ``path(sha, target_key_id)`` (atomic S3)
        2. flip the cohort's ``encryption_key_id`` to target (atomic DB)
        3. best-effort delete the old path; strays are cleaned by verify

        Caller must hold ``transaction.atomic()`` and the per-sha256
        advisory lock for the duration.
        """
        if target_key_id != self.active_key_id:
            raise ValueError(
                f"rotate_blob target_key_id={target_key_id} must match "
                f"active_key_id={self.active_key_id}"
            )
        if blob.encryption_key_id == target_key_id:
            return False

        old_key_id = blob.encryption_key_id
        sha256 = bytes(blob.sha256)

        if blob.storage_location == BlobStorageLocationChoices.POSTGRES:
            if blob.raw_content is None:
                return False
            decrypted = self.decrypt(blob.raw_content, old_key_id, sha256)
            encrypted, new_id = self.encrypt(decrypted, sha256)
            blob.raw_content = encrypted
            blob.encryption_key_id = new_id
            blob.save(
                update_fields=["raw_content", "encryption_key_id", "size_compressed"]
            )
            return True

        # OBJECT_STORAGE: read → re-encrypt → write new path → flip DB → drop old path.
        # The cohort sharing this storage object is (sha, key_id) — every
        # row in it shares the same compression value too (set at first
        # upload), but compression isn't part of the path so we don't
        # need to filter on it.
        old_path = self.compute_storage_key(sha256, old_key_id)
        new_path = self.compute_storage_key(sha256, target_key_id)

        with self.storage.open(old_path, "rb") as f:
            old_encrypted = f.read()
        decrypted = self.decrypt(old_encrypted, old_key_id, sha256)
        encrypted, new_id = self.encrypt(decrypted, sha256)

        self.storage.save(new_path, ContentFile(encrypted))

        # pylint: disable-next=import-outside-toplevel
        from core.models import Blob

        Blob.objects.filter(
            sha256=sha256,
            storage_location=BlobStorageLocationChoices.OBJECT_STORAGE,
            encryption_key_id=old_key_id,
        ).update(encryption_key_id=new_id)

        # Defer the old-path delete until the surrounding transaction
        # commits. If we ran it inline and the transaction rolled back
        # (deadlock retry at commit, network blip, caller raises), the
        # cohort would revert to old_key_id but the old path bytes
        # would already be gone — readers would compute the old path
        # and 404. Strays from a failed on_commit are detectable
        # offline via ``verify_blobs --mode=storage-to-db``.
        def _drop_old_path(path=old_path):
            try:
                self.storage.delete(path)
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Failed to drop rotated-from path %s: %s", path, e)

        transaction.on_commit(_drop_old_path)

        return True

    def re_store_blob_in_database(self, blob: "Blob", target_key_id: int) -> bool:
        """Pull an OBJECT_STORAGE blob back into PostgreSQL.

        Downloads the ciphertext, decrypts under its current key,
        re-encrypts under ``target_key_id`` (which MUST equal the active
        key — same constraint as ``rotate_blob``; passthrough when
        active_key_id=0), writes ``raw_content`` and flips
        ``storage_location`` to POSTGRES atomically. Then opportunistically
        deletes the S3 object if no rows still reference it (the
        last re-stored row in a cohort triggers the deletion; earlier
        ones see ``refs > 0`` and no-op).

        Returns True if re-stored, False if already POSTGRES / unrestorable.

        Caller must hold ``transaction.atomic()`` and the per-sha256
        advisory lock for the duration.
        """
        if target_key_id != self.active_key_id:
            raise ValueError(
                f"re_store_blob_in_database target_key_id={target_key_id} must match "
                f"active_key_id={self.active_key_id}"
            )
        if blob.storage_location != BlobStorageLocationChoices.OBJECT_STORAGE:
            return False
        if not self.enabled:
            raise RuntimeError("Object storage is not configured")

        sha256 = bytes(blob.sha256)
        old_key_id = blob.encryption_key_id
        old_path = self.compute_storage_key(sha256, old_key_id)

        with self.storage.open(old_path, "rb") as f:
            old_encrypted = f.read()
        decrypted = self.decrypt(old_encrypted, old_key_id, sha256)
        encrypted, new_id = self.encrypt(decrypted, sha256)

        blob.raw_content = encrypted
        blob.encryption_key_id = new_id
        blob.storage_location = BlobStorageLocationChoices.POSTGRES
        blob.save(
            update_fields=[
                "raw_content",
                "encryption_key_id",
                "storage_location",
                "size_compressed",
            ]
        )

        # Defer S3 cleanup of the old cohort path until commit. Running
        # it inline would delete the bucket object before the row's
        # POSTGRES flip is durable; a commit-time failure (deadlock
        # retry, network blip) would then leave the row pointing at
        # OBJECT_STORAGE while the bytes are gone — irrecoverable.
        # ``delete_if_orphaned`` re-checks the cohort count itself, so
        # if a different row in the cohort is still OBJECT_STORAGE
        # at on_commit time the bytes stay put.
        self.defer_delete_if_orphaned(sha256, old_key_id)

        return True

    def defer_delete_if_orphaned(self, sha256_bytes: bytes, key_id: int) -> None:
        """Schedule a ``delete_if_orphaned(sha, key_id)`` for transaction commit.

        Errors from the deferred call are swallowed and logged: the row
        delete has already committed, so propagating the error would
        surface it far from the original transaction. Strays are
        detectable offline via ``verify_blobs --mode=storage-to-db``.
        """

        def _run(s=sha256_bytes, k=key_id):
            try:
                self.delete_if_orphaned(s, k)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "Post-commit S3 cleanup failed for sha=%s key_id=%d; "
                    "verify_blobs --mode=storage-to-db will list strays",
                    s.hex()[:8],
                    k,
                )

        transaction.on_commit(_run)

    def delete_if_orphaned(self, sha256_bytes: bytes, key_id: int) -> bool:
        """Delete the storage object at ``path(sha, key_id)`` if no blob row
        references it.

        With key_id encoded in the path, only blobs whose
        ``(sha256, encryption_key_id)`` pair matches can possibly need
        this object — a sibling at a different key_id has its own path,
        and compression is shared across the whole cohort (set at first
        upload). An ``.exists()`` probe at this exact pair is sufficient.
        """
        if not self.enabled:
            return False

        # pylint: disable-next=import-outside-toplevel
        from core.models import Blob

        if Blob.objects.filter(
            sha256=sha256_bytes,
            storage_location=BlobStorageLocationChoices.OBJECT_STORAGE,
            encryption_key_id=key_id,
        ).exists():
            return False

        key = self.compute_storage_key(sha256_bytes, key_id)
        # Let storage errors propagate — the cleanup task catches transient
        # ones (BotoCoreError/OSError) and retries; permanent errors surface
        # in logs instead of being silently swallowed as "still referenced".
        self.storage.delete(key)
        logger.info("Deleted orphaned storage object: %s", key)
        return True

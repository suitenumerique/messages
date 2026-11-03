"""Tests for the export_spam_messages management command.

These tests write to the real ``message-exports`` test bucket, each under its
own prefix. Exports are encrypted to a test age key configured in
``STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS`` unless a test says otherwise, and
parts are decrypted to be checked.
"""

# pylint: disable=redefined-outer-name

import hashlib
import io
import json
import secrets
import tarfile
import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.files.storage import storages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

import pyrage
import pytest
from pyrage import x25519

from core import factories, models
from core.enums import BlobStorageLocationChoices
from core.services.exporter.spam import SpamExporter, SpamExportFilters
from core.services.tiered_storage import TieredStorageService

SPAM_A = b"From: a@spam.test\r\nSubject: Win\r\n\r\nYou won!\r\n"
SPAM_B = b"From: b@spam.test\r\nSubject: Cheap\r\n\r\nBuy now\r\n"
HAM = b"From: friend@example.com\r\nSubject: Hi\r\n\r\nHello\r\n"

IDENTITY = x25519.Identity.generate()
RECIPIENT = str(IDENTITY.to_public())


def sha256_hex(content: bytes) -> str:
    """SHA-256 of ``content``, as named in the export."""
    return hashlib.sha256(content).hexdigest()


@pytest.fixture(autouse=True)
def age_recipients(settings):
    """Configure the test age key as the export recipient."""
    settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = [RECIPIENT]
    return settings


@pytest.fixture
def s3():
    """The S3 client and bucket of the export storage."""
    storage = storages["message-exports"]
    return storage.connection.meta.client, storage.bucket_name


@pytest.fixture
def prefix(s3):
    """A prefix of its own for each test, emptied afterwards."""
    prefix = f"tests/spam/{uuid.uuid4()}"
    yield prefix
    client, bucket = s3
    for key in list_keys(s3, prefix):
        client.delete_object(Bucket=bucket, Key=key)


def read_object(s3, key: str) -> bytes:
    """Download one object of the export bucket."""
    client, bucket = s3
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def list_keys(s3, prefix: str) -> list[str]:
    """Keys under ``prefix`` in the export bucket."""
    client, bucket = s3
    response = client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/")
    return sorted(obj["Key"] for obj in response.get("Contents", []))


def export(prefix: str, *args: str, stdout=None) -> None:
    """Run the command on ``prefix``, encrypted to the configured test key."""
    call_command(
        "export_spam_messages",
        "--prefix",
        prefix,
        *args,
        stdout=stdout or StringIO(),
    )


def read_part(
    s3, prefix: str, index: int, identity=IDENTITY
) -> tuple[dict[str, bytes], list[dict]]:
    """Extract a part: its EMLs by file name, and its manifest records.

    The part is decrypted with ``identity``; ``None`` reads a plaintext part.
    """
    part_name = f"part-{index:05d}"
    if identity is None:
        raw = read_object(s3, f"{prefix}/{part_name}.tar.gz")
    else:
        raw = pyrage.decrypt(
            read_object(s3, f"{prefix}/{part_name}.tar.gz.age"), [identity]
        )
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            files[member.name] = tar.extractfile(member).read()
    manifest = [
        json.loads(line)
        for line in files.pop(f"{part_name}/manifest.jsonl").splitlines()
    ]
    emls = {
        name.removeprefix(f"{part_name}/"): content for name, content in files.items()
    }
    return emls, manifest


def read_state(s3, prefix: str) -> dict:
    """The ``state.json`` of an export."""
    return json.loads(read_object(s3, f"{prefix}/state.json"))


def make_message(content: bytes, *, mailbox=None, **kwargs) -> models.Message:
    """A received message holding ``content``, in a thread of ``mailbox``."""
    thread = factories.ThreadFactory()
    factories.ThreadAccessFactory(
        thread=thread, mailbox=mailbox or factories.MailboxFactory()
    )
    return factories.MessageFactory(thread=thread, raw_mime=content, **kwargs)


@pytest.fixture
def corpus():
    """Two distinct spams, one delivered twice, plus what must be left out."""
    domain = factories.MailDomainFactory(name="victim.test")
    mailbox = factories.MailboxFactory(domain=domain)
    spam_a1 = make_message(SPAM_A, mailbox=mailbox, is_spam=True)
    spam_a2 = make_message(SPAM_A, is_spam=True, is_trashed=True)
    spam_b = make_message(SPAM_B, is_spam=True)
    make_message(HAM)
    make_message(b"Subject: draft\r\n\r\nx", is_spam=True, is_draft=True)
    make_message(b"Subject: sent\r\n\r\nx", is_spam=True, is_sender=True)
    return {"spam_a1": spam_a1, "spam_a2": spam_a2, "spam_b": spam_b}


@pytest.mark.django_db
class TestDryRun:
    """``--dry-run`` sizes the export without writing."""

    def test_counts_spam_and_distinct_emls(self, corpus, prefix, s3):
        """Only received spam is counted, and EMLs are deduplicated by hash."""
        stdout = StringIO()
        call_command(
            "export_spam_messages", "--dry-run", "--prefix", prefix, stdout=stdout
        )

        output = stdout.getvalue()
        assert "Spam messages: 3" in output
        assert "Distinct EMLs (by sha256): 2" in output
        assert "victim.test: 1" in output
        assert list_keys(s3, prefix) == []


@pytest.mark.django_db
class TestExport:
    """A full export run."""

    def test_exports_raw_emls_deduplicated_with_manifest(self, corpus, prefix, s3):
        """Each spam EML is exported once, byte for byte, with its messages."""
        export(prefix)

        assert list_keys(s3, prefix) == [
            f"{prefix}/part-00001.tar.gz.age",
            f"{prefix}/state.json",
        ]
        emls, manifest = read_part(s3, prefix, 1)
        assert emls == {
            f"{sha256_hex(SPAM_A)}.eml": SPAM_A,
            f"{sha256_hex(SPAM_B)}.eml": SPAM_B,
        }

        records = {record["sha256"]: record for record in manifest}
        assert set(records) == {sha256_hex(SPAM_A), sha256_hex(SPAM_B)}
        record_a = records[sha256_hex(SPAM_A)]
        assert record_a["path"] == f"part-00001/{sha256_hex(SPAM_A)}.eml"
        assert record_a["size"] == len(SPAM_A)
        assert record_a["error"] is None
        assert {m["id"] for m in record_a["messages"]} == {
            str(corpus["spam_a1"].id),
            str(corpus["spam_a2"].id),
        }
        message_a1 = next(
            m for m in record_a["messages"] if m["id"] == str(corpus["spam_a1"].id)
        )
        assert message_a1["mime_id"] == corpus["spam_a1"].mime_id
        assert message_a1["is_trashed"] is False
        # The corpus is about content: who received it is left out.
        assert "mailboxes" not in message_a1
        assert "thread_id" not in message_a1

        state = read_state(s3, prefix)
        assert state["completed_at"] is not None
        assert state["parts"] == 1
        assert state["emls"] == 2
        assert state["messages"] == 3
        assert state["errors"] == 0
        assert state["last_sha256"] == max(sha256_hex(SPAM_A), sha256_hex(SPAM_B))

    def test_manifest_keeps_only_pipeline_verdicts_of_postmark(self, prefix, s3):
        """Verdicts are exported; the envelope recipient address is not."""
        make_message(
            SPAM_A,
            is_spam=True,
            postmark={
                "auth": "fail",
                "spam": "likely",
                "processing": "fail",
                "rcpt_to": "alias@victim.test",
            },
        )
        make_message(SPAM_B, is_spam=True)

        export(prefix)

        _, manifest = read_part(s3, prefix, 1)
        postmarks = {r["sha256"]: r["messages"][0]["postmark"] for r in manifest}
        assert postmarks[sha256_hex(SPAM_A)] == {
            "auth": "fail",
            "spam": "likely",
            "processing": "fail",
        }
        assert postmarks[sha256_hex(SPAM_B)] == {}

    def test_splits_parts_by_batch_size(self, corpus, prefix, s3):
        """``--batch-size`` caps the distinct EMLs of a part."""
        export(
            prefix,
            "--batch-size",
            "1",
        )

        first, _ = read_part(s3, prefix, 1)
        second, _ = read_part(s3, prefix, 2)
        assert len(first) == len(second) == 1
        assert {*first, *second} == {
            f"{sha256_hex(SPAM_A)}.eml",
            f"{sha256_hex(SPAM_B)}.eml",
        }
        assert read_state(s3, prefix)["parts"] == 2

    def test_exports_encrypted_offloaded_blob_decrypted(self, prefix, s3):
        """A blob encrypted and offloaded to S3 is exported as plain EML."""
        key = {"algo": "aes-gcm", "secret": secrets.token_hex(32), "active": True}
        with override_settings(MESSAGES_BLOBS_ENCRYPT_KEYS={"1": key}):
            message = make_message(SPAM_A, is_spam=True)
            blob = message.blob
            assert blob.encryption_key_id == 1
            service = TieredStorageService()
            service.upload_blob(blob)
            blob.storage_location = BlobStorageLocationChoices.OBJECT_STORAGE
            blob.raw_content = None
            blob.save()
            try:
                export(prefix)
            finally:
                service.storage.delete(
                    TieredStorageService.compute_storage_key_for_blob(blob)
                )

        emls, _ = read_part(s3, prefix, 1)
        assert emls == {f"{sha256_hex(SPAM_A)}.eml": SPAM_A}

    def test_many_emls_across_workers_and_parts(self, prefix, s3):
        """Concurrent reads keep every EML, in hash order across parts."""
        contents = [
            f"Subject: spam {i}\r\n\r\nbody {i}\r\n".encode() for i in range(10)
        ]
        for content in contents:
            make_message(content, is_spam=True)

        export(
            prefix,
            "--batch-size",
            "3",
            "--workers",
            "2",
        )

        exported = []
        for index in range(1, 5):
            emls, manifest = read_part(s3, prefix, index)
            assert [r["sha256"] for r in manifest] == sorted(
                r["sha256"] for r in manifest
            )
            exported.extend(emls.items())
        assert dict(exported) == {
            f"{sha256_hex(content)}.eml": content for content in contents
        }
        assert [name for name, _ in exported] == sorted(name for name, _ in exported)
        assert read_state(s3, prefix)["parts"] == 4

    def test_blob_is_fetched_once_for_all_its_messages(self, corpus, prefix):
        """A spam delivered to many mailboxes holds one copy of its content."""
        exporter = SpamExporter(prefix, age_recipients=[RECIPIENT])
        filters = SpamExportFilters(until=timezone.now())

        (entry,) = exporter._window_entries(
            filters, (bytes.fromhex(sha256_hex(SPAM_A)),)
        )

        assert len(entry.messages) == 2
        assert entry.messages[0].blob is entry.messages[1].blob

    def test_rows_are_fetched_one_window_at_a_time(self, prefix, s3):
        """A part never holds more than one window of messages and contents."""
        for i in range(10):
            make_message(f"Subject: spam {i}\r\n\r\nbody\r\n".encode(), is_spam=True)
        exporter = SpamExporter(
            prefix, age_recipients=[RECIPIENT], batch_size=10, workers=1
        )

        with mock.patch.object(
            SpamExporter,
            "_window_entries",
            autospec=True,
            side_effect=SpamExporter._window_entries,
        ) as window_entries:
            state = exporter.run()

        # One part of 10 hashes, read in windows of workers * 4 = 4 hashes.
        assert [len(call.args[2]) for call in window_entries.call_args_list] == [
            4,
            4,
            2,
        ]
        assert state.parts == 1
        assert state.emls == 10

    def test_no_spam_completes_without_part(self, prefix, s3):
        """An export with nothing to export still records its completion."""
        make_message(HAM)
        export(prefix)

        assert list_keys(s3, prefix) == [f"{prefix}/state.json"]
        assert read_state(s3, prefix)["completed_at"] is not None

    def test_content_not_matching_its_hash_is_not_exported(self, corpus, prefix, s3):
        """A blob reading back other bytes than its hash is reported, not exported."""
        original = models.Blob.get_content

        def get_content(blob):
            if bytes(blob.sha256).hex() == sha256_hex(SPAM_B):
                return b"corrupted"
            return original(blob)

        with mock.patch.object(models.Blob, "get_content", get_content):
            export(prefix)

        emls, manifest = read_part(s3, prefix, 1)
        assert list(emls) == [f"{sha256_hex(SPAM_A)}.eml"]
        failed = next(r for r in manifest if r["sha256"] == sha256_hex(SPAM_B))
        assert failed["path"] is None
        assert "does not match its sha256" in failed["error"]
        assert read_state(s3, prefix)["errors"] == 1

    def test_unreadable_blob_is_reported_not_fatal(self, corpus, prefix, s3):
        """A blob that fails to read is listed with its error and skipped."""
        original = models.Blob.get_content

        def get_content(blob):
            if bytes(blob.sha256).hex() == sha256_hex(SPAM_B):
                raise ValueError("boom")
            return original(blob)

        with mock.patch.object(models.Blob, "get_content", get_content):
            export(prefix)

        emls, manifest = read_part(s3, prefix, 1)
        assert list(emls) == [f"{sha256_hex(SPAM_A)}.eml"]
        failed = next(r for r in manifest if r["sha256"] == sha256_hex(SPAM_B))
        assert failed["path"] is None
        assert failed["error"] == "ValueError: boom"
        assert read_state(s3, prefix)["errors"] == 1


@pytest.mark.django_db
class TestFilters:
    """``--maildomain``, ``--since`` and ``--until``."""

    def test_maildomain(self, corpus, prefix, s3):
        """Only spam delivered to the mail domain is exported."""
        export(
            prefix,
            "--maildomain",
            "victim.test",
        )

        emls, manifest = read_part(s3, prefix, 1)
        assert list(emls) == [f"{sha256_hex(SPAM_A)}.eml"]
        assert [m["id"] for m in manifest[0]["messages"]] == [str(corpus["spam_a1"].id)]

    def test_since_until(self, prefix, s3):
        """Only spam received within [since, until) is exported."""
        now = timezone.now()
        old = make_message(SPAM_A, is_spam=True)
        recent = make_message(SPAM_B, is_spam=True)
        models.Message.objects.filter(pk=old.pk).update(
            created_at=now - timedelta(days=10)
        )
        models.Message.objects.filter(pk=recent.pk).update(
            created_at=now - timedelta(days=2)
        )

        export(
            prefix,
            "--since",
            (now - timedelta(days=5)).date().isoformat(),
            "--until",
            now.isoformat(),
        )

        emls, _ = read_part(s3, prefix, 1)
        assert list(emls) == [f"{sha256_hex(SPAM_B)}.eml"]

    @pytest.mark.parametrize(
        "value", ["not-a-date", "2026-02-30", "2026-01-01T25:00:00"]
    )
    def test_invalid_date_is_rejected(self, value):
        """Malformed and impossible dates both fail as a command error."""
        with pytest.raises(CommandError, match="Invalid date"):
            call_command("export_spam_messages", "--dry-run", "--since", value)

    def test_since_after_until_is_rejected(self, prefix):
        """An empty window is a mistake, not an empty export."""
        with pytest.raises(CommandError, match="--since must be before --until"):
            call_command(
                "export_spam_messages",
                "--prefix",
                prefix,
                "--since",
                "2026-02-01",
                "--until",
                "2026-01-01",
            )


@pytest.mark.django_db
class TestResume:
    """Rerunning on the prefix of an export."""

    def _interrupt_after_first_part(self, s3, prefix):
        """Rewind a completed two-part export to as if it stopped after part 1."""
        client, bucket = s3
        state = read_state(s3, prefix)
        first, _ = read_part(s3, prefix, 1)
        state.update(
            completed_at=None,
            parts=1,
            last_sha256=next(iter(first)).removesuffix(".eml"),
            emls=1,
            messages=1,
        )
        client.put_object(
            Bucket=bucket, Key=f"{prefix}/state.json", Body=json.dumps(state)
        )
        client.delete_object(Bucket=bucket, Key=f"{prefix}/part-00002.tar.gz.age")
        return first

    def test_resumes_after_last_exported_part(self, corpus, prefix, s3):
        """An interrupted export picks up after its last part.

        Rerun with its prefix alone, it keeps its filters and age recipients.
        """
        export(
            prefix,
            "--batch-size",
            "1",
        )
        first = self._interrupt_after_first_part(s3, prefix)

        stdout = StringIO()
        call_command("export_spam_messages", "--prefix", prefix, stdout=stdout)

        assert "Resuming export" in stdout.getvalue()
        second, _ = read_part(s3, prefix, 2)
        assert len(second) == 1
        assert not set(first) & set(second)
        state = read_state(s3, prefix)
        assert state["completed_at"] is not None
        assert state["parts"] == 2
        assert state["emls"] == 2

    def test_resume_refuses_other_filters(self, corpus, prefix, s3):
        """Resuming with other filters would mix two exports in one prefix."""
        export(
            prefix,
            "--batch-size",
            "1",
        )
        self._interrupt_after_first_part(s3, prefix)

        with pytest.raises(CommandError, match="other filters"):
            call_command(
                "export_spam_messages",
                "--prefix",
                prefix,
                "--maildomain",
                "victim.test",
            )

    def test_resume_keeps_its_recipients(self, corpus, prefix, s3, settings):
        """Keys configured since the start do not apply to a resumed export.

        Otherwise its parts would not all be readable by the same keys.
        """
        export(prefix, "--batch-size", "1")
        self._interrupt_after_first_part(s3, prefix)
        other = x25519.Identity.generate()
        settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = [str(other.to_public())]

        stdout = StringIO()
        export(prefix, stdout=stdout)

        assert "keeps encrypting to its original ones" in stdout.getvalue()
        second, _ = read_part(s3, prefix, 2)
        assert len(second) == 1
        with pytest.raises(pyrage.DecryptError):
            read_part(s3, prefix, 2, identity=other)
        assert read_state(s3, prefix)["age_recipients"] == [RECIPIENT]

    def test_completed_export_is_not_redone(self, corpus, prefix, s3):
        """Rerunning a completed export writes nothing new."""
        export(prefix)
        make_message(b"Subject: late\r\n\r\nspam", is_spam=True)

        stdout = StringIO()
        call_command("export_spam_messages", "--prefix", prefix, stdout=stdout)

        assert "already completed" in stdout.getvalue()
        assert read_state(s3, prefix)["emls"] == 2

    def test_completed_export_refuses_other_filters(self, corpus, prefix, s3):
        """A completed export does not pass for one with other filters."""
        export(prefix)

        with pytest.raises(CommandError, match="other filters"):
            export(prefix, "--maildomain", "victim.test")


@pytest.mark.django_db
class TestEncryption:
    """Parts are encrypted to the configured age keys, or plaintext on request."""

    def test_parts_are_age_encrypted(self, corpus, prefix, s3):
        """The stored part is an age file, unreadable without the private key."""
        stdout = StringIO()
        export(prefix, stdout=stdout)

        raw = read_object(s3, f"{prefix}/part-00001.tar.gz.age")
        assert raw.startswith(b"age-encryption.org/v1\n")
        assert SPAM_A not in raw
        with pytest.raises(pyrage.DecryptError):
            pyrage.decrypt(raw, [x25519.Identity.generate()])
        assert read_state(s3, prefix)["age_recipients"] == [RECIPIENT]
        assert "encrypted to 1 age recipient(s)" in stdout.getvalue()

    def test_each_recipient_can_decrypt(self, corpus, prefix, s3, settings):
        """Every configured key can read the export."""
        other = x25519.Identity.generate()
        settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = [
            RECIPIENT,
            str(other.to_public()),
        ]
        export(prefix)

        for identity in (IDENTITY, other):
            emls, _ = read_part(s3, prefix, 1, identity=identity)
            assert emls[f"{sha256_hex(SPAM_A)}.eml"] == SPAM_A

    def test_new_export_requires_an_encryption_choice(
        self, corpus, prefix, s3, settings
    ):
        """Without configured keys, plaintext is never the silent default."""
        settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = []
        with pytest.raises(
            CommandError, match="STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS"
        ):
            call_command("export_spam_messages", "--prefix", prefix)
        assert list_keys(s3, prefix) == []

    def test_no_encryption_writes_plaintext(self, corpus, prefix, s3, settings):
        """--no-encryption writes plain tar.gz parts, and says so."""
        settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = []
        stdout = StringIO()
        call_command(
            "export_spam_messages",
            "--prefix",
            prefix,
            "--no-encryption",
            stdout=stdout,
        )

        emls, _ = read_part(s3, prefix, 1, identity=None)
        assert emls[f"{sha256_hex(SPAM_B)}.eml"] == SPAM_B
        assert read_state(s3, prefix)["age_recipients"] == []
        assert "NOT encrypted" in stdout.getvalue()

    def test_invalid_recipient_is_rejected(self, corpus, prefix, s3, settings):
        """A malformed configured key fails before anything is written."""
        settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = ["age1bad"]
        with pytest.raises(CommandError, match="Invalid age recipient"):
            export(prefix)
        assert list_keys(s3, prefix) == []

    def test_no_encryption_refused_when_recipients_are_configured(self, prefix, s3):
        """Plaintext cannot be forced where exports are meant to be encrypted."""
        with pytest.raises(CommandError, match="--no-encryption is refused"):
            export(prefix, "--no-encryption")
        assert list_keys(s3, prefix) == []

    def test_public_keys_cannot_be_given_on_the_command_line(self, prefix):
        """Keys only come from the configuration."""
        with pytest.raises(CommandError):
            export(prefix, "--age-recipient", RECIPIENT)

    def test_dry_run_reports_the_encryption(self, corpus, prefix, settings):
        """The dry run tells whether the export would be encrypted."""
        stdout = StringIO()
        call_command("export_spam_messages", "--dry-run", stdout=stdout)
        assert "Encryption: 1 age recipient(s) configured" in stdout.getvalue()

        settings.STORAGE_MESSAGE_EXPORTS_AGE_RECIPIENTS = []
        stdout = StringIO()
        call_command("export_spam_messages", "--dry-run", stdout=stdout)
        assert "no age recipient configured" in stdout.getvalue()


@pytest.mark.django_db
def test_requires_a_configured_bucket(corpus, prefix, settings):
    """Without an export bucket, the command fails before doing anything."""
    storages_settings = {**settings.STORAGES, "message-exports": {"OPTIONS": {}}}
    with override_settings(STORAGES=storages_settings):
        with pytest.raises(CommandError, match="STORAGE_MESSAGE_EXPORTS_"):
            call_command("export_spam_messages", "--prefix", prefix)


def test_exporter_rejects_non_positive_sizes():
    """A zero batch size would never make progress."""
    with pytest.raises(Exception, match="must be positive"):
        SpamExporter("tests/spam/x", batch_size=0)

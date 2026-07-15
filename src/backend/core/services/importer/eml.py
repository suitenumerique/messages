"""Single-message (.eml) import runner.

A oneshot delivery of one raw message from the imports bucket. The ``cursor``
watermark is binary — 0 (not yet delivered) or 1 (done) — so a resume of an
already-delivered eml is a no-op.
"""

from django.conf import settings

from .utils import deliver, imports_storage, run_plan


def run_eml(channel, state) -> tuple[int, int, int]:
    """Deliver the single message; idempotent on resume via the 0/1 cursor."""
    recipient = channel.mailbox
    file_key = (channel.settings or {})["import"]["file_key"]
    storage, s3_client = imports_storage()

    # Fail an oversized .eml up front with an explanatory error: raising here
    # propagates to ``run_import_task``, which marks the run FAILED with this
    # text — instead of a silent failure_count=1 with no message for the user.
    limit = settings.MAX_INCOMING_EMAIL_SIZE
    size = s3_client.head_object(Bucket=storage.bucket_name, Key=file_key).get(
        "ContentLength", 0
    )
    if size > limit:
        raise ValueError(
            f"File too large: {size} bytes (the per-message limit is {limit} bytes)."
        )

    def deliver_item(_item):
        # Cap the fetch at the per-message limit (+1 byte) so an oversized object
        # is truncated here — and rejected by deliver() — instead of downloaded
        # whole. deliver() enforces the size limit, so no second check is needed.
        resp = s3_client.get_object(
            Bucket=storage.bucket_name, Key=file_key, Range=f"bytes=0-{limit}"
        )
        return deliver(resp["Body"].read(), recipient, channel)

    return run_plan(channel, state, [file_key], deliver_item)

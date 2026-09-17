"""Flip the mobile OTA channel manifest to the release staged by this deploy.

Runs as the tail of the Scalingo `postdeploy` hook (see the Procfile), which the
platform only executes on a successful deployment — that timing is the whole
design: the bundle and its immutable release metadata were uploaded during the
build (the frontend `scalingo-postbuild` npm script, which runs
deploy/paas/scalingo_stage_ota → `publish-ota.mjs --stage-only`), but
the channel manifest, the pointer devices actually follow, only moves here. The
OTA channel therefore can never advertise a version that is not serving, and
redeploying an old commit re-flips the manifest backward (the manifest
`sequence` counter, not the build id, orders releases).

Python mirror of `manifestKey`/`releaseKey`/`bundleKey`/`computeNextSequence`/
`validateChannel`/`validateVersion` and of the manifest-write step of
`publish-ota.mjs`, all in `src/frontend/scripts/ota-lib.mjs` — kept in sync by
hand: node and `src/` no longer exist in the postdeploy container (see
scalingo_postcompile), while boto3 ships with the backend dependencies.
"""

import json
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

# The app root: deploy/paas/<this file> → two levels up. The postdeploy
# container runs from /app but resolving from __file__ keeps the script
# runnable from anywhere.
APP_ROOT = Path(__file__).resolve().parents[2]
MARKER = APP_ROOT / "build" / "ota-release-id"

# Mirrors of validateChannel / validateVersion (anti path-traversal: these
# values become S3 key segments).
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _log(message: str) -> None:
    """Print a build-log line immediately (postdeploy output is streamed)."""
    print(f"-----> {message}", flush=True)  # noqa: T201


def _fail(message: str) -> None:
    """Abort the postdeploy hook: Scalingo marks the deployment `hook-error`
    and keeps the previous version serving — manifest untouched, mobile and web
    stay consistent."""
    print(f"OTA promote failed: {message}", file=sys.stderr, flush=True)  # noqa: T201
    sys.exit(1)


def _read_json(client, bucket: str, key: str):
    """Fetch and parse a JSON object, or return None when it does not exist
    (mirror of ota-lib's readJson)."""
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise
    return json.load(response["Body"])


def main() -> None:
    """Read the deploy's staged-release marker and re-point the manifest."""
    if not MARKER.exists():
        _log("No staged OTA release for this deploy, skipping manifest flip")
        return

    bucket = os.environ.get("MOBILE_OTA_S3_BUCKET")
    if not bucket:
        # Theoretical on Scalingo (build and runtime share one env), but a
        # leftover marker without OTA config must not fail an OTA-less deploy.
        _log("build/ota-release-id present but MOBILE_OTA_S3_BUCKET unset, skipping")
        return

    parts = MARKER.read_text(encoding="utf-8").split()
    if len(parts) != 2:
        _fail(f"malformed marker {MARKER}: {parts!r}")
    version, channel = parts
    if not VERSION_RE.match(version):
        _fail(f"invalid version in marker: {version!r}")
    if not CHANNEL_RE.match(channel):
        _fail(f"invalid channel in marker: {channel!r}")

    # Mirror of otaConfig(): path-style addressing (forcePathStyle) and the
    # optional key prefix, normalized with a trailing slash.
    prefix = os.environ.get("MOBILE_OTA_S3_KEY_PREFIX", "")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MOBILE_OTA_S3_ENDPOINT"],
        aws_access_key_id=os.environ["MOBILE_OTA_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MOBILE_OTA_S3_SECRET_KEY"],
        region_name=os.environ.get("MOBILE_OTA_S3_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}),
    )

    manifest_key = f"{prefix}channels/{channel}/manifest.json"
    release_key = f"{prefix}channels/{channel}/releases/{version}.json"
    bundle_key = f"{prefix}channels/{channel}/bundles/{version}.zip"

    release = _read_json(client, bucket, release_key)
    if release is None:
        _fail(
            f"no release metadata at '{release_key}' — the build staged "
            f"{version} but its artifacts are missing"
        )
    # The zip is what devices will download: make sure the pointer will not
    # dangle (mirror of rollback-ota.mjs's HeadObject check).
    try:
        client.head_object(Bucket=bucket, Key=bundle_key)
    except ClientError:
        _fail(f"bundle zip missing at '{bundle_key}'")

    manifest = _read_json(client, bucket, manifest_key)
    if manifest is not None and manifest.get("version") == version:
        # Hook retry or redeploy of the very same commit: nothing to move.
        _log(f"OTA manifest already at {version} (channel '{channel}'), skipping")
        return

    # Mirror of computeNextSequence: bumped on every manifest write so devices
    # order releases by this counter, never by the build id — which is what
    # makes a redeploy of an old commit an effective rollback.
    current = manifest.get("sequence") if manifest else None
    sequence = (current if isinstance(current, int) and current > 0 else 0) + 1

    client.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps({**release, "sequence": sequence}),
        ContentType="application/json",
        # Never let a CDN serve a stale manifest: it is the freshness signal
        # (mirror of writeManifest).
        CacheControl="no-cache",
    )
    _log(
        f"OTA manifest flipped to {version} (sequence {sequence}, "
        f"channel '{channel}')"
    )


if __name__ == "__main__":
    main()

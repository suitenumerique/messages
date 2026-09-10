// Shared S3 configuration for the mobile OTA publishing scripts. The bundles and
// the per-channel manifests live on a public bucket; publishing runs from the
// frontend toolchain (dev: `make ota-*` against RustFS; prod: CI against the
// target S3). Django is deliberately not involved — the OTA release is a
// frontend artifact.
import {
  GetObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";

/** Read a required env var or fail fast with a clear message. */
export const requireEnv = (name) => {
  const value = process.env[name];
  if (!value) {
    console.error(`Missing required environment variable: ${name}`);
    process.exit(1);
  }
  return value;
};

/**
 * Build the S3 client and the key layout shared by publish/bucket scripts.
 *
 * `MOBILE_OTA_S3_KEY_PREFIX` lets a shared bucket host several apps under a path
 * (e.g. `messages/mobileapp/`); it defaults to empty (dedicated bucket root).
 * It only affects the object *keys* — the public read URL is derived from
 * `MOBILE_OTA_PUBLIC_BASE_URL`, which the operator sets consistently with the prefix.
 */
export const otaConfig = () => {
  const rawPrefix = process.env.MOBILE_OTA_S3_KEY_PREFIX ?? "";
  const prefix =
    rawPrefix && !rawPrefix.endsWith("/") ? `${rawPrefix}/` : rawPrefix;
  return {
    bucket: requireEnv("MOBILE_OTA_S3_BUCKET"),
    prefix,
    client: new S3Client({
      endpoint: requireEnv("MOBILE_OTA_S3_ENDPOINT"),
      // RustFS (dev) and most S3-compatible stores need path-style addressing.
      forcePathStyle: true,
      region: process.env.MOBILE_OTA_S3_REGION || "us-east-1",
      credentials: {
        accessKeyId: requireEnv("MOBILE_OTA_S3_ACCESS_KEY"),
        secretAccessKey: requireEnv("MOBILE_OTA_S3_SECRET_KEY"),
      },
    }),
  };
};

/**
 * Validate a channel name so it stays safe as an S3 key segment and URL path.
 * Exits with a clear message on anything else.
 */
export const validateChannel = (channel) => {
  if (!/^[a-z0-9][a-z0-9._-]*$/.test(channel)) {
    console.error(
      `Invalid channel name '${channel}': lowercase letters, digits, ` +
        "'.', '_' and '-' only.",
    );
    process.exit(1);
  }
  return channel;
};

/**
 * Validate a version id so it stays safe as a local filename and S3 key
 * segment (it names the bundle zip). Exits with a clear message on anything
 * else — a `/` or `..` would escape the intended paths.
 */
export const validateVersion = (version) => {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(version)) {
    console.error(
      `Invalid version '${version}': must start with a letter or digit, ` +
        "then letters, digits, '.', '_' and '-' only.",
    );
    process.exit(1);
  }
  return version;
};

/**
 * S3 key of a channel's manifest — the mutable pointer the apps poll. Each
 * channel is self-contained (its bundles live under `channels/<channel>/`
 * too): builds inline the NEXT_PUBLIC_* env, so bundles are never shared or
 * copied across channels.
 */
export const manifestKey = (prefix, channel) => {
  return `${prefix}channels/${channel}/manifest.json`;
};

/**
 * S3 key of a release's immutable metadata, written at publish time. The
 * encrypted checksum and sessionKey of a version exist nowhere else once the
 * channel manifest moves on, so this file is what makes an already-published
 * bundle re-pointable by `rollback-ota.mjs`.
 */
export const releaseKey = (prefix, channel, version) => {
  return `${prefix}channels/${channel}/releases/${version}.json`;
};

/**
 * S3 key of a version's bundle zip under its channel.
 */
export const bundleKey = (prefix, channel, version) => {
  return `${prefix}channels/${channel}/bundles/${version}.zip`;
};

/**
 * Next value of the channel's monotonic release counter. Incremented on every
 * manifest write — publish, forced republish and rollback alike — so devices
 * can order releases independently of the build ids they point to (that is
 * what lets a rollback go "backward" in build terms).
 */
export const computeNextSequence = (existingManifest) => {
  const current = existingManifest?.sequence;
  return (Number.isInteger(current) && current > 0 ? current : 0) + 1;
};

/**
 * Parse the monotonic ordering prefix of a legacy hybrid `<count>-<sha>`
 * version. Current ids are bare short shas (returns null — count guards
 * self-disable, `sequence` rules). Mirrors `versionCount()` in
 * src/features/native/ota.ts (browser vs node context, kept in sync by hand).
 */
export const versionCount = (version) => {
  const match = /^(\d+)-/.exec(version ?? "");
  return match ? Number(match[1]) : null;
};

/** Read and parse a JSON object, or return null when it does not exist. */
export const readJson = async ({ client, bucket }, key) => {
  try {
    const response = await client.send(
      new GetObjectCommand({ Bucket: bucket, Key: key }),
    );
    return JSON.parse(await response.Body.transformToString());
  } catch (error) {
    if (error?.name === "NoSuchKey" || error?.$metadata?.httpStatusCode === 404) {
      return null;
    }
    throw error;
  }
};

/** Write a channel manifest. */
export const writeManifest = async ({ client, bucket }, key, manifest) => {
  await client.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: key,
      Body: JSON.stringify(manifest),
      ContentType: "application/json",
      // Never let a CDN serve a stale manifest: it is the freshness signal.
      CacheControl: "no-cache",
    }),
  );
};

/** Write a release's immutable metadata (default caching is fine: it never changes). */
export const writeRelease = async ({ client, bucket }, key, release) => {
  await client.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: key,
      Body: JSON.stringify(release),
      ContentType: "application/json",
    }),
  );
};

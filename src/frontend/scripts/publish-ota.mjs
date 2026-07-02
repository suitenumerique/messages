// Publish a *signed* mobile OTA bundle. The bucket is world-readable, so a
// substituted zip would be arbitrary code in the WebView: every bundle is
// therefore encrypted+signed (Capgo v2, RSA+AES) with a per-instance private
// key before upload. The native updater verifies it against the public key
// baked into the app (see capacitor.config.ts, `publicKey`).
//
// Flow: `capgo bundle zip` (→ plaintext sha256) → `capgo bundle encrypt`
// (→ encrypted `*_encrypted.zip`, an encrypted checksum and an ivSessionKey) →
// upload the encrypted zip as `channels/<channel>/bundles/<version>.zip` →
// write `channels/<channel>/manifest.json` carrying that checksum + sessionKey,
// which the app passes back to CapacitorUpdater.download() (see
// src/features/native/ota.ts).
//
// Each channel is a self-contained folder, bundles included: the NEXT_PUBLIC_*
// vars are inlined into the web bundle at build time, so a staging build is NOT
// a prod build — never copy a bundle across channels, rebuild and republish for
// each. Keeping the zips under the channel also stops two channels publishing
// the same commit (same <count>-<sha> id) from overwriting each other.
//
// Usage: node scripts/publish-ota.mjs --version <x.y.z> [--channel <name>]
//        [--dist ./dist] [--force]
// The channel falls back to the MOBILE_OTA_CHANNEL env var.
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";

import { PutObjectCommand } from "@aws-sdk/client-s3";

import {
  manifestKey,
  otaConfig,
  readManifest,
  requireEnv,
  validateChannel,
  validateVersion,
  versionCount,
  writeManifest,
} from "./ota-lib.mjs";

const { values } = parseArgs({
  options: {
    version: { type: "string" },
    channel: { type: "string" },
    dist: { type: "string", default: "./dist" },
    force: { type: "boolean", default: false },
  },
});

const channel = values.channel ?? process.env.MOBILE_OTA_CHANNEL;
if (!values.version || !channel) {
  console.error(
    "Usage: node scripts/publish-ota.mjs --version <x.y.z> --channel <name> " +
      "[--dist ./dist] [--force]\n" +
      "The channel may also come from the MOBILE_OTA_CHANNEL env var.",
  );
  process.exit(1);
}
validateChannel(channel);
validateVersion(values.version);

const { version, dist } = values;

if (!existsSync(dist) || !statSync(dist).isDirectory()) {
  console.error(`Dist directory not found: ${dist}`);
  process.exit(1);
}

// Run a Capgo CLI subcommand and return the JSON it prints on stdout (`--json`).
// The CLI decorates progress on stderr, so we slice from the first `{` to the
// last `}` to stay robust against any stray prefix.
const capgo = (args) => {
  const out = execFileSync("npx", ["--no-install", "@capgo/cli", ...args], {
    encoding: "utf8",
  });
  const json = out.slice(out.indexOf("{"), out.lastIndexOf("}") + 1);
  return JSON.parse(json);
};

const appId = process.env.MOBILE_APP_ID ?? "local.suitenumerique.messages";

// The CLI writes the zip (and its `*_encrypted.zip` sibling) next to the cwd.
const zipName = `${version}.zip`;
const encryptedZip = `${zipName}_encrypted.zip`;

const { client, bucket, prefix } = otaConfig();
const channelManifestKey = manifestKey(prefix, channel);

// Publish-side mirror of the client's downgrade guard (see ota.ts): devices
// would refuse a manifest whose count is not strictly greater than what they
// run, so pushing one to the channel could only strand it. Fail fast — before
// the zip/encrypt work — unless it is an idempotent republish of the exact
// same version (e.g. a CI retry).
const existing = await readManifest({ client, bucket }, channelManifestKey);
if (existing && existing.version !== version && !values.force) {
  const currentCount = versionCount(existing.version);
  const nextCount = versionCount(version);
  if (currentCount !== null && nextCount !== null && nextCount <= currentCount) {
    console.error(
      `Refusing to publish ${version} to channel '${channel}': it does not ` +
        `order above the current ${existing.version} and devices would ` +
        "ignore it. Publish a higher-count build (git revert + republish), " +
        "or pass --force if you really know better.",
    );
    process.exit(1);
  }
}

let keyDir;
try {
  // Decode the base64 PEM private key into a locked-down temp file: the CLI
  // takes a key path, and base64 keeps the multi-line PEM to a single env
  // line. Written inside the try — in a private, unpredictable mkdtemp dir —
  // so the finally always wipes it, whatever fails below.
  keyDir = mkdtempSync(join(tmpdir(), "ota-signing-"));
  const keyPath = join(keyDir, "private.pem");
  writeFileSync(
    keyPath,
    Buffer.from(
      requireEnv("MOBILE_OTA_SIGNING_PRIVATE_KEY_B64"),
      "base64",
    ).toString("utf8"),
    { mode: 0o600 },
  );

  // 1. Zip `dist/` (index.html at the root) and get its plaintext sha256.
  const { checksum: plainChecksum } = capgo([
    "bundle",
    "zip",
    appId,
    "--path",
    dist,
    "--name",
    zipName,
    "--key-v2",
    "--no-code-check",
    "--json",
  ]);

  // 2. Encrypt+sign. Emits the encrypted zip, the encrypted checksum and the
  //    ivSessionKey; the last two travel in the manifest for native verification.
  const { checksum, ivSessionKey } = capgo([
    "bundle",
    "encrypt",
    zipName,
    plainChecksum,
    "--key",
    keyPath,
    "--json",
  ]);

  // 3. Upload the *encrypted* zip under the channel (see the header comment:
  //    channels never share bundles).
  const body = readFileSync(encryptedZip);
  const bundleKey = `${prefix}channels/${channel}/bundles/${version}.zip`;
  await client.send(
    new PutObjectCommand({
      Bucket: bucket,
      Key: bundleKey,
      Body: body,
      ContentType: "application/zip",
    }),
  );

  // Public base URL the device uses to reach the bucket — kept separate from the
  // S3 endpoint the script writes to. In dev the script uploads to
  // objectstorage:9000 (compose network) while the emulator reads the manifest
  // from localhost:8906 (adb reverse); in prod they are the CDN vs the bucket.
  const publicBaseUrl = requireEnv("MOBILE_OTA_PUBLIC_BASE_URL").replace(/\/+$/, "");
  const manifest = {
    version,
    url: `${publicBaseUrl}/channels/${channel}/bundles/${version}.zip`,
    checksum,
    sessionKey: ivSessionKey,
  };
  await writeManifest({ client, bucket }, channelManifestKey, manifest);

  console.log(
    `Published signed OTA bundle ${version} (${body.length} bytes) to ` +
      `'${bucket}/${bundleKey}' and updated '${channelManifestKey}' ` +
      `(channel '${channel}').`,
  );
} finally {
  for (const path of [zipName, encryptedZip]) {
    rmSync(path, { force: true });
  }
  if (keyDir) {
    rmSync(keyDir, { recursive: true, force: true });
  }
}

// Roll a channel back to an already-published OTA version, without rebuilding
// anything: re-point `channels/<channel>/manifest.json` at the release metadata
// archived by publish-ota.mjs (`channels/<channel>/releases/<version>.json`)
// under a *higher* `sequence`. Devices order releases by that counter, not by
// the build id, so they follow the manifest "backward" — that is the whole
// point of the sequence field (see src/features/native/ota.ts).
//
// Limits, printed as they apply:
// - Releases published before the sequence era have no archived metadata (the
//   encrypted checksum/sessionKey lived only in the overwritten manifest);
//   remediation is to check out the old git ref and republish.
// - Devices running a pre-sequence client only compare build counts and will
//   ignore the rollback until they receive a sequence-aware build.
// - A device that blacklisted the target version (it failed to boot there)
//   will skip it; recovery for those is a new forward publish.
//
// Usage: node scripts/rollback-ota.mjs --version <id> [--channel <name>]
// The channel falls back to the MOBILE_OTA_CHANNEL env var.
import { parseArgs } from "node:util";

import { HeadObjectCommand } from "@aws-sdk/client-s3";

import {
  bundleKey,
  computeNextSequence,
  manifestKey,
  otaConfig,
  readJson,
  releaseKey,
  validateChannel,
  validateVersion,
  writeManifest,
} from "./ota-lib.mjs";

const { values } = parseArgs({
  options: {
    version: { type: "string" },
    channel: { type: "string" },
  },
});

const channel = values.channel ?? process.env.MOBILE_OTA_CHANNEL;
if (!values.version || !channel) {
  console.error(
    "Usage: node scripts/rollback-ota.mjs --version <id> [--channel <name>]\n" +
      "The channel may also come from the MOBILE_OTA_CHANNEL env var.",
  );
  process.exit(1);
}
validateChannel(channel);
validateVersion(values.version);
const { version } = values;

const { client, bucket, prefix } = otaConfig();
const env = { client, bucket };

const release = await readJson(env, releaseKey(prefix, channel, version));
if (!release) {
  console.error(
    `No archived release metadata for ${version} on channel '${channel}' ` +
      `('${releaseKey(prefix, channel, version)}' not found). Either the ` +
      "version was never published on this channel, or it predates release " +
      "archiving — its encrypted checksum/sessionKey are lost with the old " +
      "manifest. Remediation: check out that git ref and `make mobile-ota-publish` " +
      "it again (the new sequence makes devices follow it).",
  );
  process.exit(1);
}

// The bundle zip is what devices will actually download — make sure it is
// still there before pointing the whole channel at it.
try {
  await client.send(
    new HeadObjectCommand({
      Bucket: bucket,
      Key: bundleKey(prefix, channel, version),
    }),
  );
} catch {
  console.error(
    `Bundle '${bundleKey(prefix, channel, version)}' is missing from ` +
      `'${bucket}': the archived metadata points at nothing. Republish the ` +
      "version instead of rolling back to it.",
  );
  process.exit(1);
}

const existing = await readJson(env, manifestKey(prefix, channel));
if (existing?.version === version) {
  console.warn(
    `Channel '${channel}' already points at ${version} ` +
      `(sequence ${existing.sequence ?? "none"}); nothing to do.`,
  );
  process.exit(0);
}

const manifest = {
  ...release,
  sequence: computeNextSequence(existing),
  rolledBackAt: new Date().toISOString(),
};
await writeManifest(env, manifestKey(prefix, channel), manifest);

console.log(
  `Rolled channel '${channel}' back to ${version} (sequence ` +
    `${manifest.sequence}, was ${existing?.version ?? "unpublished"}).\n` +
    "Devices apply it at next launch or foreground (30 min throttle). " +
    "Devices still on a pre-sequence client will NOT downgrade, and any " +
    "device that blacklisted this version after a failed boot will skip it.",
);

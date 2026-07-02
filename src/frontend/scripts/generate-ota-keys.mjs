// Generate a per-instance OTA signing key pair (Capgo v2, RSA-2048, PKCS1 PEM).
// Prints both halves base64-encoded (single line) so they drop straight into an
// env file or a CI secret:
//   - MOBILE_OTA_SIGNING_PUBLIC_KEY_B64  → baked into the app by capacitor.config.ts,
//     lets the native updater verify each downloaded bundle.
//   - MOBILE_OTA_SIGNING_PRIVATE_KEY_B64 → used by publish-ota.mjs to sign bundles.
//     KEEP IT SECRET (CI secret in prod); never commit a real one.
//
// Each deployment (La Suite operator) runs this once and stores its own pair —
// the two halves must stay a matched set or the app rejects its own bundles.
//
// Usage: node scripts/generate-ota-keys.mjs
import { generateKeyPairSync } from "node:crypto";

// PKCS1 ("BEGIN RSA … KEY") is the format the Capgo CLI produces and expects;
// PKCS8 keys are rejected by `bundle encrypt` with "Invalid private key format".
const { publicKey, privateKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "pkcs1", format: "pem" },
  privateKeyEncoding: { type: "pkcs1", format: "pem" },
});

const b64 = (pem) => Buffer.from(pem, "utf8").toString("base64");

// Guidance goes to stderr so stdout stays a clean, pipeable KEY=VALUE pair.
process.stderr.write(
  "New OTA signing pair. Put the PUBLIC half in the app build env and the\n" +
    "PRIVATE half in a secret (CI); both must come from the SAME run.\n\n",
);
process.stdout.write(`MOBILE_OTA_SIGNING_PUBLIC_KEY_B64=${b64(publicKey)}\n`);
process.stdout.write(`MOBILE_OTA_SIGNING_PRIVATE_KEY_B64=${b64(privateKey)}\n`);

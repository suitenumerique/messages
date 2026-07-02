import { CapacitorHttp } from "@capacitor/core";
import { CapacitorUpdater } from "@capgo/capacitor-updater";

import { isNativePlatform } from "./platform";

/**
 * Over-The-Air update of the JS bundle, driven entirely from S3 — no Capgo
 * server is involved (see capacitor.config.ts, autoUpdate: false). The app
 * reads a manifest published next to the bundles and, when it advertises a
 * newer version than the one currently running, downloads the zip and swaps
 * the WebView onto it.
 *
 * Bundles are encrypted+signed (Capgo v2, RSA+AES) zips of `dist/` uploaded to a
 * public bucket; the manifest is `{ version, url, checksum, sessionKey }`, the
 * last two feeding native signature verification. See the `ota-publish` target.
 */
type OtaManifest = {
  version: string;
  url: string;
  // Both come from `capgo bundle encrypt` at publish time (see publish-ota.mjs).
  // With signing on, `checksum` is the *encrypted* checksum and `sessionKey` is
  // the RSA-wrapped AES session key; the native layer verifies both against the
  // baked-in public key. They are absent only for legacy unsigned bundles.
  checksum?: string;
  sessionKey?: string;
};

/**
 * Mirror of the plugin's "last failed update" record (a bundle rolled back for
 * never calling notifyAppReady). The native record self-clears on first read,
 * so it is copied here to keep the blacklist across launches. WebView storage
 * is per-origin, not per-bundle, so it survives the rollback itself.
 */
const OTA_BOOT_FAILED_KEY = "ota-boot-failed-version";

/**
 * Parse the monotonic ordering prefix of a hybrid `<count>-<sha>` bundle version
 * (see docs/mobile.md, "Bundle versioning"). Returns null for ids without it —
 * the literal `"builtin"`, or a manually pinned non-hybrid version — which then
 * fall back to the plain inequality check (no ordering enforced).
 */
const versionCount = (version: string): number | null => {
  const match = /^(\d+)-/.exec(version);
  return match ? Number(match[1]) : null;
};

/**
 * Confirm the running bundle booted successfully. Without this call the plugin
 * assumes the update is broken and rolls back to the previous bundle on the
 * next launch, so it must run as early as the app is functional.
 */
export const notifyOtaAppReady = async (): Promise<void> => {
  if (!isNativePlatform()) {
    return;
  }
  try {
    await CapacitorUpdater.notifyAppReady();
  } catch (error) {
    console.error("OTA notifyAppReady failed", error);
  }
};

/**
 * Fetch the manifest and, if it points to a version other than the active
 * bundle, download and activate it. `set()` reloads the WebView, so on success
 * this never returns normally. Fire-and-forget at startup: failures are logged
 * and leave the current bundle untouched.
 *
 * The manifest URL comes from the resolved app configuration (backend
 * MOBILE_OTA_MANIFEST_URL, /config endpoint) so the followed channel can
 * change without shipping a new native build; unset disables OTA.
 */
export const checkAndApplyOtaUpdate = async (
  manifestUrl: string | undefined,
): Promise<void> => {
  if (!isNativePlatform() || !manifestUrl) {
    return;
  }

  // Hot reload session: the app is served by the Vite dev server (DEV) with
  // MOBILE_DEV_SERVER_URL baked as server.url (capacitor.config.ts). Applying
  // an OTA there would reload the WebView onto a downloaded bundle, killing
  // the session. An embedded dev build (MOBILE_DEV_SERVER_URL unset) still
  // exercises the full OTA chain.
  if (import.meta.env.DEV && import.meta.env.MOBILE_DEV_SERVER_URL) {
    return;
  }

  // The manifest URL is server-driven, so it can no longer prove at build time
  // that the bundle-verification public key was baked in (capacitor.config.ts).
  // Without that key the native layer would apply an unverified zip from the
  // world-readable bucket — refuse instead: a key-less build is not OTA-capable.
  if (!import.meta.env.MOBILE_OTA_SIGNING_PUBLIC_KEY_B64) {
    console.warn(
      "OTA manifest URL configured but this build embeds no signing public " +
        "key (MOBILE_OTA_SIGNING_PUBLIC_KEY_B64); skipping unverifiable update.",
    );
    return;
  }

  try {
    // Routed through the native HTTP layer: reaches the cleartext dev bucket
    // and sidesteps WebView CORS.
    const response = await CapacitorHttp.get({ url: manifestUrl });
    const manifest = response.data as OtaManifest;
    // CapacitorHttp resolves even on HTTP errors, so a 403/404 (manifest not
    // published yet) lands here with an S3 error body instead of JSON. Fail
    // fast with a clear log rather than deep inside download().
    if (
      typeof manifest?.version !== "string" ||
      typeof manifest?.url !== "string"
    ) {
      console.warn(
        `OTA manifest at ${manifestUrl} is malformed or missing ` +
          `(HTTP ${response.status}); skipping.`,
      );
      return;
    }

    const { bundle } = await CapacitorUpdater.current();
    if (manifest.version === bundle.version) {
      return;
    }

    // Downgrade/replay guard: only ever move forward. The hybrid version's
    // leading count is monotonic, so a manifest whose count is not strictly
    // greater than the running bundle's is an accidental old publish (or a
    // replayed old bundle) — refuse it. Ids without a count prefix can't be
    // ordered, so they fall through to the inequality check above and still
    // apply (dev / non-hybrid builds).
    const currentCount = versionCount(bundle.version);
    const nextCount = versionCount(manifest.version);
    if (currentCount !== null && nextCount !== null && nextCount <= currentCount) {
      console.warn(
        `OTA manifest ${manifest.version} is not newer than the running ` +
          `${bundle.version}; skipping to avoid a downgrade.`,
      );
      return;
    }

    // Boot-loop guard: a bundle that failed to boot (never called
    // notifyAppReady) was auto-reverted by the plugin, which records it as the
    // last failed update. Re-applying it would just crash and revert again,
    // forever — so refuse a version already known bad. Recovery is a *new*
    // higher-count publish (see docs/mobile.md, "Rollback"). Unlike bundle
    // statuses in `list()`, this record is boot-specific: a transient
    // download/install failure never sets it, so those versions stay
    // retryable.
    const failed = await CapacitorUpdater.getFailedUpdate();
    if (failed?.bundle.version) {
      localStorage.setItem(OTA_BOOT_FAILED_KEY, failed.bundle.version);
    }
    if (localStorage.getItem(OTA_BOOT_FAILED_KEY) === manifest.version) {
      console.warn(
        `OTA ${manifest.version} previously failed to boot; skipping.`,
      );
      return;
    }

    const next = await CapacitorUpdater.download({
      url: manifest.url,
      version: manifest.version,
      checksum: manifest.checksum,
      sessionKey: manifest.sessionKey,
    });
    await CapacitorUpdater.set(next);
  } catch (error) {
    console.error("OTA update check failed", error);
  }
};

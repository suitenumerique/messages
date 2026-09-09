import { App } from "@capacitor/app";
import { CapacitorHttp } from "@capacitor/core";
import { CapacitorUpdater } from "@capgo/capacitor-updater";
import type { BundleInfo } from "@capgo/capacitor-updater";

import { compareVersionCounts } from "./ota-version.mjs";
import { isNativePlatform } from "./platform";

/**
 * Over-The-Air update of the JS bundle, driven entirely from S3 — no Capgo
 * server is involved (see capacitor.config.ts, autoUpdate: false). The app
 * reads a manifest published next to the bundles and, when it advertises a
 * different release, downloads the zip and *stages* it (CapacitorUpdater.next):
 * the update applies when the app backgrounds or relaunches, or immediately
 * when the user taps the update toast (see use-ota-update-toast.tsx) — no more
 * mid-session WebView reload.
 *
 * Bundles are encrypted+signed (Capgo v2, RSA+AES) zips of `dist/` uploaded to a
 * public bucket; the manifest carries `checksum` + `sessionKey` feeding native
 * signature verification (a manifest missing either is refused outright, see
 * isSignedManifest), and a monotonic `sequence` ordering releases
 * independently of build ids — which is what lets a rollback (redeploying an
 * older commit on Scalingo, `make mobile-ota-rollback` elsewhere) move a
 * channel back to an older build. See docs/mobile-release.md, "Rollback".
 */
type OtaManifest = {
  version: string;
  url: string;
  // Both come from `capgo bundle encrypt` at publish time (see publish-ota.mjs):
  // `checksum` is the *encrypted* checksum and `sessionKey` the RSA-wrapped AES
  // session key; the native layer verifies both against the baked-in public
  // key. Typed optional only because the manifest is untrusted input — a
  // manifest missing either is refused (see isSignedManifest), never applied.
  checksum?: string;
  sessionKey?: string;
  // Monotonic release counter bumped by every publish AND rollback. Absent on
  // manifests written before the rollback era ("legacy"), which then fall back
  // to the build-count ordering below.
  sequence?: number;
  publishedAt?: string;
};

/**
 * Mirror of the plugin's "last failed update" record (a bundle rolled back for
 * never calling notifyAppReady). The native record self-clears on first read,
 * so it is copied here to keep the blacklist across launches. WebView storage
 * is per-origin, not per-bundle, so it survives the rollback itself.
 */
const OTA_BOOT_FAILED_KEY = "ota-boot-failed-version";

/**
 * Highest manifest `sequence` this device has staged. The floor only ever
 * moves up, which is the anti-replay guard: a stale manifest (CDN cache, old
 * publish) can never drag the device backward — only a *new* manifest with a
 * higher sequence can, and that is precisely a rollback. Stored in
 * localStorage like the blacklist above; losing it merely re-enables
 * trust-on-first-use, bounded by bundle signatures: whoever can write the
 * manifest decides which signed release a fresh device follows, and that is
 * the same authority a rollback needs.
 */
const OTA_SEQUENCE_KEY = "ota-applied-sequence";

/** Minimum delay between two manifest checks triggered by app foregrounding. */
const OTA_RESUME_CHECK_INTERVAL_MS = 30 * 60 * 1000;

type SignedOtaManifest = OtaManifest & { checksum: string; sessionKey: string };

/**
 * Only https reaches the network in a release build; a dev build also talks
 * to the cleartext local bucket (MOBILE_ALLOW_CLEARTEXT_FOR_DEV).
 */
const isTransportAllowed = (url: string): boolean =>
  import.meta.env.DEV || url.startsWith("https://");

/** Both signature fields present and non-empty — the only manifests ever applied. */
const isSignedManifest = (manifest: OtaManifest): manifest is SignedOtaManifest =>
  typeof manifest.checksum === "string" &&
  manifest.checksum.length > 0 &&
  typeof manifest.sessionKey === "string" &&
  manifest.sessionKey.length > 0;

const readAppliedSequence = (): number | null => {
  const raw = localStorage.getItem(OTA_SEQUENCE_KEY);
  if (raw === null) {
    return null;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
};

const persistAppliedSequence = (sequence: number): void => {
  const current = readAppliedSequence();
  if (current === null || sequence > current) {
    localStorage.setItem(OTA_SEQUENCE_KEY, String(sequence));
  }
};

/**
 * Module-level staged-update store, so the React side (the update toast) can
 * learn that a bundle is ready without importing the plugin. `subscribe`
 * replays a bundle staged before the subscriber mounted — the boot check
 * usually finishes before the layout (and its toaster) exists.
 */
let stagedBundle: BundleInfo | null = null;
const stagedSubscribers = new Set<(bundle: BundleInfo) => void>();

export const subscribeOtaUpdateStaged = (
  callback: (bundle: BundleInfo) => void,
): (() => void) => {
  stagedSubscribers.add(callback);
  if (stagedBundle) {
    callback(stagedBundle);
  }
  return () => {
    stagedSubscribers.delete(callback);
  };
};

/**
 * Apply the staged bundle now. `reload()` destroys the current JS context, so
 * on success this never returns normally.
 */
export const applyStagedOtaUpdate = async (): Promise<void> => {
  try {
    await CapacitorUpdater.reload();
  } catch (error) {
    console.error("OTA reload failed", error);
  }
};

/**
 * Hold back the staged bundle until {@link releaseOtaInstall}.
 *
 * A staged bundle is installed by the *native* layer as soon as the app is
 * backgrounded — `appMovedToBackground()` calls `installNext()`, which sets
 * the bundle and reloads the WebView, whatever `autoUpdate` says. Opening the
 * system browser backgrounds the app, so without this hold every login that
 * follows a publish reloads the WebView mid-flow and destroys the JS context
 * the flow lives in (see auth.ts). A delay condition is the plugin's own
 * mechanism for it: `installNext()` returns early while one is set.
 *
 * `kind: "kill"` only names the event that would clear the condition on its
 * own; the release below is what normally lifts it, and a process death
 * during the flow leaves no condition behind either way.
 */
export const holdOtaInstall = async (): Promise<void> => {
  if (!isNativePlatform()) {
    return;
  }
  try {
    await CapacitorUpdater.setMultiDelay({ delayConditions: [{ kind: "kill" }] });
  } catch (error) {
    console.warn("OTA install hold failed", error);
  }
};

/**
 * Lift the hold set by {@link holdOtaInstall}, so the staged bundle installs
 * at the next background again. Must run on every exit path of the held flow:
 * a condition left behind would freeze OTA updates until the app is killed.
 */
export const releaseOtaInstall = async (): Promise<void> => {
  if (!isNativePlatform()) {
    return;
  }
  try {
    await CapacitorUpdater.cancelDelay();
  } catch (error) {
    console.warn("OTA install release failed", error);
  }
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

let inFlightCheck: Promise<void> | null = null;
let lastCheckAt = 0;

/**
 * Fetch the manifest and, if it advertises a release this device should move
 * to, download the bundle and stage it via `next()` — the current session is
 * never interrupted. Fire-and-forget at startup: failures are logged and leave
 * the current bundle untouched.
 *
 * The manifest URL comes from the resolved app configuration (backend
 * MOBILE_OTA_MANIFEST_URL, /config endpoint) so the followed channel can
 * change without shipping a new native build; unset disables OTA.
 */
export const checkAndStageOtaUpdate = async (
  manifestUrl: string | undefined,
): Promise<void> => {
  if (!isNativePlatform() || !manifestUrl) {
    return;
  }

  // Hot reload session: the app is served by the Vite dev server (DEV) with
  // MOBILE_DEV_SERVER_URL baked as server.url (capacitor.config.ts). Staging
  // an OTA there would swap the WebView onto a downloaded bundle at the next
  // background, killing the session. An embedded dev build
  // (MOBILE_DEV_SERVER_URL unset) still exercises the full OTA chain.
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

  // Release builds ship without the cleartext exception (the gradle and Xcode
  // release phases strip it), and a plain-http manifest would hand the choice
  // of what to stage to whoever sits on the network. Dev builds keep it for
  // the local bucket.
  if (!isTransportAllowed(manifestUrl)) {
    console.warn(
      `OTA manifest URL ${manifestUrl} is not https; refusing to fetch it ` +
        "outside a dev build.",
    );
    return;
  }

  // Boot and resume can both trigger a check while one is still running:
  // sharing the in-flight promise keeps a single fetch/download/stage cycle.
  if (inFlightCheck) {
    return inFlightCheck;
  }
  inFlightCheck = runOtaCheck(manifestUrl).finally(() => {
    inFlightCheck = null;
  });
  return inFlightCheck;
};

const runOtaCheck = async (manifestUrl: string): Promise<void> => {
  lastCheckAt = Date.now();
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
    // Fail closed on a manifest that carries no signature material. The
    // Android plugin only runs the RSA verification when `sessionKey` is set;
    // with it empty it falls back to comparing the *plain* checksum the
    // manifest itself advertises — which the manifest's author controls — so an
    // unsigned zip would be accepted as genuine. Refusing here keeps the
    // baked-in public key meaningful on every platform, and a refused manifest
    // must not touch the sequence floor either.
    if (!isSignedManifest(manifest)) {
      console.warn(
        `OTA manifest at ${manifestUrl} carries no checksum/sessionKey; ` +
          "refusing to stage an unverifiable bundle.",
      );
      return;
    }
    // The signature covers the zip, not where it is fetched from, and the
    // manifest itself can carry the bundle URL anywhere: same rule as for
    // the manifest URL.
    if (!isTransportAllowed(manifest.url)) {
      console.warn(
        `OTA bundle URL ${manifest.url} is not https; refusing to download it ` +
          "outside a dev build.",
      );
      return;
    }
    // A malformed sequence demotes the manifest to legacy ordering rather than
    // being trusted as one.
    const sequence =
      typeof manifest.sequence === "number" && Number.isFinite(manifest.sequence)
        ? manifest.sequence
        : null;

    const { bundle, native } = await CapacitorUpdater.current();
    if (manifest.version === bundle.version) {
      // Already there — but keep the anti-replay floor moving so a later
      // stale manifest cannot look "new" to this device.
      if (sequence !== null) {
        persistAppliedSequence(sequence);
      }
      return;
    }

    // Boot-loop guard: a bundle that failed to boot (never called
    // notifyAppReady) was auto-reverted by the plugin, which records it as the
    // last failed update. Re-applying it would just crash and revert again,
    // forever — so refuse a version already known bad, and let it win over a
    // rollback pointing at it: recovery for such a device is a *new* publish
    // (see docs/mobile.md, "Rollback"). Unlike bundle statuses in `list()`,
    // this record is boot-specific: a transient download/install failure never
    // sets it, so those versions stay retryable.
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

    if (sequence !== null) {
      // Sequence-aware manifest: releases are ordered by the publish counter,
      // not by build ids — a lower-count version with a higher sequence is a
      // rollback and must be followed. Two floors still apply:
      //
      // Native floor — never install a JS bundle older than the one shipped
      // inside the binary, so the JS/native plugin surfaces cannot drift
      // apart. Only hybrid-era ids can be ordered this way: with sha ids on
      // both sides the comparison is null and the floor self-disables — the
      // compensating rule is operational (cut store builds from a commit at
      // or behind the deployed channel, see docs/mobile-release.md).
      const nativeOrder = compareVersionCounts(manifest.version, native);
      if (nativeOrder !== null && nativeOrder < 0) {
        console.warn(
          `OTA manifest ${manifest.version} is older than the native build ` +
            `${native}; skipping.`,
        );
        return;
      }
      // Sequence floor — refuse a manifest this device has already seen
      // applied (a stale/replayed pointer). No persisted floor yet means the
      // device predates sequences or lost its storage: accept and start the
      // floor here (trust-on-first-use), which is exactly what lets those
      // devices follow a rollback.
      const applied = readAppliedSequence();
      if (applied !== null && sequence <= applied) {
        console.warn(
          `OTA manifest sequence ${sequence} is not newer than the applied ` +
            `${applied}; skipping stale manifest.`,
        );
        return;
      }
    } else {
      // Legacy manifest (pre-sequence): only ever move forward along the
      // hybrid version's monotonic leading count. Ids without a count prefix
      // can't be ordered, so they fall through to the inequality check above
      // and still apply (dev / non-hybrid builds).
      const runningOrder = compareVersionCounts(manifest.version, bundle.version);
      if (runningOrder !== null && runningOrder <= 0) {
        console.warn(
          `OTA manifest ${manifest.version} is not newer than the running ` +
            `${bundle.version}; skipping to avoid a downgrade.`,
        );
        return;
      }
    }

    // A rollback target may still sit in the local bundle store from when it
    // was active — reuse it instead of re-downloading. Only a fully installed
    // copy qualifies; anything else re-enters the download path.
    const { bundles } = await CapacitorUpdater.list();
    const local = bundles.find(
      (candidate) =>
        candidate.version === manifest.version &&
        candidate.status === "success",
    );
    const target =
      local ??
      (await CapacitorUpdater.download({
        url: manifest.url,
        version: manifest.version,
        checksum: manifest.checksum,
        sessionKey: manifest.sessionKey,
      }));

    await CapacitorUpdater.next({ id: target.id });
    // Only after next() succeeded: a transient download/stage failure must
    // leave the floor untouched so the release stays retryable.
    if (sequence !== null) {
      persistAppliedSequence(sequence);
    }

    stagedBundle = target;
    for (const subscriber of stagedSubscribers) {
      subscriber(target);
    }
  } catch (error) {
    console.error("OTA update check failed", error);
  }
};

/**
 * Re-check the manifest when the app comes back to the foreground, so
 * long-running sessions (and emergency rollbacks) don't wait for the next cold
 * start. Throttled, and idle while an update is already staged — that one
 * applies at the very next background anyway (Capgo `next()` semantics), at
 * which point the relaunched app checks again.
 */
export const listenForOtaUpdatesOnResume = (
  manifestUrl: string | undefined,
): void => {
  if (!isNativePlatform() || !manifestUrl) {
    return;
  }
  void App.addListener("resume", () => {
    if (
      stagedBundle ||
      inFlightCheck ||
      Date.now() - lastCheckAt < OTA_RESUME_CHECK_INTERVAL_MS
    ) {
      return;
    }
    void checkAndStageOtaUpdate(manifestUrl);
  });
};

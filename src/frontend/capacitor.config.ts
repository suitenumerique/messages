import type { CapacitorConfig } from "@capacitor/cli";

// OTA signing public key, per-instance and injected at `cap sync` time from a
// base64-encoded PEM (single line, so it survives docker env_file / CI secrets).
// The matching private key signs bundles at publish time (see publish-ota.mjs);
// baking the public half here lets the native updater verify each downloaded
// bundle. Unset is only allowed when OTA itself is off (no manifest URL, e.g.
// a web-only build) — see the guard below.
const otaPublicKeyB64 = process.env.MOBILE_OTA_SIGNING_PUBLIC_KEY_B64;
const otaPublicKey = otaPublicKeyB64
  ? Buffer.from(otaPublicKeyB64, "base64").toString("utf8")
  : undefined;

// An OTA-enabled app without a baked-in verification key would apply any
// unsigned bundle from the world-readable bucket — refuse to build. Publishing
// already requires the private half (publish-ota.mjs), so a key-less OTA build
// could never receive a legitimate update anyway. This sync-time guard only
// sees the deprecated baked manifest URL: the nominal path (URL served by the
// backend MOBILE_OTA_MANIFEST_URL setting through /config) is covered by the
// equivalent runtime refusal in src/features/native/ota.ts.
if (process.env.NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL && !otaPublicKey) {
  throw new Error(
    "NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL is set but " +
      "MOBILE_OTA_SIGNING_PUBLIC_KEY_B64 is missing: an OTA-enabled build " +
      "must embed the signing public key (run `make mobile-ota-keygen`, see " +
      "deploy/env/frontend.defaults).",
  );
}

// Release id stamped into the *builtin* bundle so a fresh store install reports
// the same version an OTA manifest advertises — otherwise the builtin reports
// the literal "builtin" and the first launch always re-downloads (see
// docs/mobile.md, "Bundle versioning"). Derived from git at build time
// (Makefile MOBILE_OTA_BUILD_ID); unset (e.g. web-only build) ⇒ the plugin falls back
// to the native versionName.
const otaBuildId = process.env.MOBILE_OTA_BUILD_ID;

// Dev-only hot reload: when set, the WebView loads the app straight from the
// Vite dev server instead of the embedded dist/, so JS/CSS changes apply
// through HMR without rebuilding or reinstalling the app. Set by default in
// deploy/env/frontend.defaults (http://localhost:8900, reachable from
// the device via adb reverse on Android / the simulator loopback on iOS);
// disable it with an empty value in frontend.local. Must NEVER be set for a
// release build — the URL is baked into the shipped config (a gradle guard
// blocks Android release builds; see android/app/build.gradle).
const devServerUrl = process.env.MOBILE_DEV_SERVER_URL;

// The store-facing version of the *native* apps (Android versionName, iOS
// MARKETING_VERSION / CFBundleShortVersionString) — the string users read in
// the store listing. Bumped manually here when cutting a store release; it
// carries no ordering constraint (that is versionCode / CURRENT_PROJECT_VERSION,
// see docs/mobile.md, App versioning).
//
// It lives here rather than in package.json because it versions the *shipped
// app*, not the web codebase: a web deploy or an OTA bundle changes the latter
// without ever reaching the stores, so the two numbers move on different
// cadences and the UI shows them as distinct values (see use-app-version.ts).
// `cap sync` copies this key verbatim into each platform's synced
// capacitor.config.json, which is the single file both native builds read —
// gradle for versionName, scripts/generate-ios-xcconfig.mjs for the xcconfig.
const appVersion = "0.1.1";

// `appVersion` is ours, not part of Capacitor's schema — the CLI copies unknown
// top-level keys into the synced config untouched, which is exactly what the
// native builds read.
const config: CapacitorConfig & { appVersion: string } = {
  appVersion,
  // Build-time app identity. The repo ships neutral placeholders; an
  // organisation publishing to the stores overrides them via the MOBILE_APP_ID
  // (signed bundle id) and MOBILE_APP_NAME (displayed name) env vars — read
  // here by `cap sync`, and by the native builds: gradle resValue/applicationId
  // on Android (android/app/build.gradle), the generated xcconfig on iOS
  // (scripts/generate-ios-xcconfig.mjs). `||` not `??`: the env files ship the
  // vars empty, and an empty string must fall back like an unset one.
  appId: process.env.MOBILE_APP_ID || "local.suitenumerique.messages",
  appName: process.env.MOBILE_APP_NAME || "ST Messages",
  webDir: "dist",
  server: {
    // Dev only: `cap sync` turns this into android:usesCleartextTraffic in the
    // Android manifest, allowing plain HTTP for the whole app process — the
    // WebView loading the Vite dev server as well as the native HTTP layer
    // reaching the http://localhost:8901 backend and the RustFS OTA bucket
    // (needed even with hot reload disabled, hence the dedicated flag). Unset
    // for release builds: the manifest then stays cleartext-free.
    cleartext: Boolean(
      devServerUrl || process.env.MOBILE_ALLOW_CLEARTEXT_FOR_DEV,
    ),
    ...(devServerUrl ? { url: devServerUrl } : {}),
  },
  plugins: {
    // Route window.fetch through the native HTTP layer: session cookies
    // live in the native jar (no SameSite/ITP restriction, works over the
    // plain-HTTP dev backend) and CORS does not apply.
    CapacitorHttp: {
      enabled: true,
    },
    CapacitorCookies: {
      enabled: true,
    },
    // Disable Capacitor 8's built-in SystemBars inset listener: combined with
    // windowSoftInputMode=adjustResize it double-applies the keyboard inset, so
    // the WebView shrinks by twice the keyboard height (capacitor #8181, the
    // Android < 15 variant). Capacitor then stops injecting the --safe-area-inset-*
    // CSS variables on Android *and* stops resizing the WebView for the keyboard
    // on Android 15+, where the forced edge-to-edge window makes adjustResize a
    // no-op; MainActivity.java takes over both, applying the keyboard inset only
    // on the versions that need it. iOS is unaffected: env(safe-area-inset-*)
    // resolves natively there.
    SystemBars: {
        insetsHandling: "disable",
    },
    // While the app is open it surfaces the mail itself, so a foreground push
    // must not banner or sound (docs/push-notifications.md §6) — only the badge
    // tracks. iOS only: Android never auto-displays in foreground, and the web
    // service worker applies the same rule on a focused window (public/sw.js).
    // Background/killed alerts are rendered by the OS from the content-free
    // loc-key payload and are unaffected.
    PushNotifications: {
      presentationOptions: ["badge"],
    },
    // OTA live updates driven entirely from JS against an S3-hosted manifest
    // (see src/features/native/ota.ts). autoUpdate is off so the plugin never
    // talks to a Capgo server: we only use its native download/set/reload.
    CapacitorUpdater: {
      autoUpdate: false,
      resetWhenUpdate: true,
      // Verify each OTA bundle against the per-instance signing key (v2, RSA+AES).
      ...(otaPublicKey ? { publicKey: otaPublicKey } : {}),
      // Report this id (not "builtin") for the shipped bundle, so the OTA
      // freshness check can match a manifest published from the same commit.
      ...(otaBuildId ? { version: otaBuildId } : {}),
    },
  },
};

export default config;

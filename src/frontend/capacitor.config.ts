import type { CapacitorConfig } from "@capacitor/cli";

// Dev-only hot reload: when set, the WebView loads the app straight from the
// Vite dev server instead of the embedded dist/, so JS/CSS changes apply
// through HMR without rebuilding or reinstalling the app. Set by default in
// env.d/development/frontend.defaults (http://localhost:8900, reachable from
// the device via adb reverse on Android / the simulator loopback on iOS);
// disable it with an empty value in frontend.local. Must NEVER be set for a
// release build — the URL is baked into the shipped config (a gradle guard
// blocks Android release builds; see android/app/build.gradle).
const devServerUrl = process.env.MOBILE_DEV_SERVER_URL;

const config: CapacitorConfig = {
  // Build-time app identity. The repo ships a neutral placeholder; an
  // organisation publishing to the stores overrides it with its own signed
  // bundle id via the MOBILE_APP_ID env var (read here by `cap sync`, and by
  // the native builds — see android/app/build.gradle and the iOS pbxproj).
  appId: process.env.MOBILE_APP_ID ?? "local.suitenumerique.messages",
  appName: "Messages",
  webDir: "dist",
  server: {
    // Dev only: allows the Android WebView to reach http://localhost:8901.
    cleartext: true,
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
    // CSS variables on Android; MainActivity.java re-injects them (without the
    // buggy keyboard handling). iOS is unaffected: env(safe-area-inset-*)
    // resolves natively there.
    SystemBars: {
        insetsHandling: "disable",
    },
  },
};

export default config;

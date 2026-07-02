/**
 * Tripwires for the cross-app SSO invariants.
 *
 * The mobile SSO rests on a handful of declarations spread across the native
 * projects. None of them fails loudly when lost: login still works, only the
 * *silent* second-app login quietly turns back into a credential prompt. The
 * most likely regressions are mechanical — regenerating the iOS project,
 * "simplifying" back to the default Browser plugin, a Keycloak realm reset —
 * so these tests pin the files themselves and turn a silent break into a red
 * CI. They cannot prove the runtime behavior: the manual two-app test in the
 * release checklist (docs/mobile.md) remains required before a store release.
 *
 * Out of reach here: the IdP-side conditions. The dev Keycloak `acr.loa.map`
 * mapping (src/keycloak/realm.json) lives outside the frontend mount the
 * tests run in — and it only covers development anyway: in production the
 * IdP is ProConnect, whose session/ACR behavior is not in this repo at all.
 * Both are only guarded by docs/mobile.md ("Cross-app SSO conditions") and
 * the manual release checklist.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (relativePath: string): string =>
  readFileSync(resolve(frontendRoot, relativePath), "utf8");

// Source of truth for the deep-link scheme (see auth.ts). Extracted from the
// source so a scheme rename fails here unless every declaration follows.
const authSource = read("src/features/native/auth.ts");
const scheme = /AUTH_CALLBACK_SCHEME = "([a-z0-9]+)"/.exec(authSource)?.[1];

describe("cross-app SSO invariants", () => {
  it("declares the deep-link scheme in auth.ts", () => {
    expect(scheme).toBeTruthy();
  });

  describe("iOS", () => {
    it("ships the ASWebAuthenticationSession plugin", () => {
      // The default Capacitor Browser plugin uses SFSafariViewController,
      // whose cookie store is isolated from Safari: the IdP session cookie
      // would not be shared across apps. If this file disappeared (e.g. an
      // iOS project regeneration), cross-app SSO is gone.
      expect(read("ios/App/App/WebAuthSessionPlugin.swift")).toContain(
        "ASWebAuthenticationSession",
      );
    });

    it("keeps the auth session NON-ephemeral", () => {
      // `prefersEphemeralWebBrowserSession = false` is what lets the session
      // share Safari's persistent cookies — the whole point of the plugin.
      // Flipping it to true keeps login working and only kills the silent SSO.
      expect(read("ios/App/App/WebAuthSessionPlugin.swift")).toContain(
        "prefersEphemeralWebBrowserSession = false",
      );
    });

    it("registers the plugin on the bridge", () => {
      // App-local plugins are not auto-discovered: without this registration
      // WebAuthSession.start() rejects and login itself breaks on iOS.
      expect(read("ios/App/App/MainViewController.swift")).toContain(
        "registerPluginInstance(WebAuthSessionPlugin())",
      );
    });

    it("registers the callback scheme in Info.plist", () => {
      // With a non-ephemeral session iOS only delivers the callback for an
      // app-registered scheme (CFBundleURLTypes).
      expect(read("ios/App/App/Info.plist")).toContain(`<string>${scheme}</string>`);
    });
  });

  describe("Android", () => {
    it("registers the callback scheme in the manifest", () => {
      // Routes the Custom Tab's deep-link redirect back to the app
      // (caught by App.addListener("appUrlOpen")).
      expect(
        read("android/app/src/main/AndroidManifest.xml"),
      ).toContain(`android:scheme="${scheme}"`);
    });
  });
});

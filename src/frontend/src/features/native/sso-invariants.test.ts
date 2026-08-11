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

// Source of truth for the deep-link scheme (see auth.ts). The scheme is
// per-environment (MOBILE_AUTH_SCHEME) so staging and production builds can
// coexist on a device, so what has to hold is no longer one literal shared by
// three files: each side must *substitute* the variable, and their fallbacks
// must agree. Either half failing strands the OIDC callback — silently, since
// the login opens normally and only the return never lands.
const authSource = read("src/features/native/auth.ts");
const schemeDefault =
  /AUTH_CALLBACK_SCHEME =[\s\S]{0,120}?\|\|\s*"([a-z][a-z0-9+.-]*)"/.exec(authSource)?.[1];

describe("cross-app SSO invariants", () => {
  it("declares the deep-link scheme in auth.ts", () => {
    expect(schemeDefault).toBeTruthy();
  });

  it("reads the scheme from the build environment", () => {
    // Hardcoding it back would silently pin every environment to one scheme,
    // and two installed builds would fight over the callback.
    expect(authSource).toContain("import.meta.env.MOBILE_AUTH_SCHEME");
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
      // app-registered scheme (CFBundleURLTypes). The value is substituted by
      // Xcode from the AUTH_CALLBACK_SCHEME build setting, which lives in the
      // gitignored generated.xcconfig — an unset setting expands to an empty
      // string rather than failing, so the point of use must carry the inline
      // default, and it must agree with the auth.ts fallback.
      expect(read("ios/App/App/Info.plist")).toContain(
        `<string>$(AUTH_CALLBACK_SCHEME:default=${schemeDefault})</string>`,
      );
    });

    it("feeds the generated xcconfig scheme from MOBILE_AUTH_SCHEME", () => {
      // generated.xcconfig (written at `make mobile-build`) is how the container
      // env reaches Xcode. A wrong fallback there would quietly diverge from
      // the scheme auth.ts builds its callback URL with — same invariant as the
      // gradle manifestPlaceholder on Android.
      const script = read("scripts/generate-ios-xcconfig.mjs");
      const fallback =
        /MOBILE_AUTH_SCHEME \|\| "([a-z][a-z0-9+.-]*)"/.exec(script)?.[1];

      expect(fallback).toBe(schemeDefault);
    });

    it("keeps the bundle identifier driven by MOBILE_APP_ID", () => {
      // Regenerating the project would hardcode the id back, silently detaching
      // it from the env (and from the synced capacitor.config.json the release
      // guard build phase compares against).
      const pbxproj = read("ios/App/App.xcodeproj/project.pbxproj");
      const bundleIdConfigs =
        pbxproj.match(
          /PRODUCT_BUNDLE_IDENTIFIER = "\$\(MOBILE_APP_ID:default=[^)]+\)";/g,
        ) ?? [];

      expect(bundleIdConfigs.length).toBeGreaterThan(0);
    });
  });

  describe("Android", () => {
    it("registers the callback scheme in the manifest", () => {
      // Routes the Custom Tab's deep-link redirect back to the app
      // (caught by App.addListener("appUrlOpen")). Substituted by gradle from
      // the manifestPlaceholder below.
      expect(read("android/app/src/main/AndroidManifest.xml")).toContain(
        'android:scheme="${authCallbackScheme}"',
      );
    });

    it("feeds the manifest placeholder from MOBILE_AUTH_SCHEME", () => {
      // A missing placeholder fails the manifest merge loudly, but a wrong
      // fallback does not: it would quietly diverge from the scheme auth.ts
      // builds its callback URL with.
      const buildGradle = read("android/app/build.gradle");
      const fallback =
        /authCallbackScheme: System\.getenv\("MOBILE_AUTH_SCHEME"\) \?: "([a-z][a-z0-9+.-]*)"/.exec(
          buildGradle,
        )?.[1];

      expect(fallback).toBe(schemeDefault);
    });
  });
});

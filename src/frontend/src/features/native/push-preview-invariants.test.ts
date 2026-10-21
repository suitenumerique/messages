/**
 * Tripwires for the device-encrypted notification previews
 * (docs/push-notifications.md §5.1).
 *
 * The native half is wiring spread across both projects — a plugin
 * registration, a manifest service swap, an extension target, a shared
 * Keychain group — and losing any piece fails silently: notifications keep
 * arriving, only as the generic banner. These tests pin the files themselves.
 * The decryption itself is covered against a backend-sealed vector by
 * PushPreviewCryptoTest (Android JUnit).
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ANDROID_NOTIFICATION_CHANNEL_ID } from "./push";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (relativePath: string): string =>
  readFileSync(resolve(frontendRoot, relativePath), "utf8");

const androidSources = "android/app/src/main/java/local/suitenumerique/messages";
const pushSource = read("src/features/native/push.ts");

/** Number of days in a `name[: Type] = <n> * 24 * 3600` constant declaration. */
const days = (source: string, name: string): number | undefined => {
  const match = new RegExp(`\\b${name}(?::\\s*\\w+)?\\s*=\\s*(\\d+)L?\\s*\\*\\s*24`).exec(
    source,
  );
  return match ? Number(match[1]) : undefined;
};

describe("notification preview invariants", () => {
  it("names the plugin the same on all three sides", () => {
    expect(pushSource).toContain('registerPlugin<PushPreviewKeysPlugin>("PushPreviewKeys")');
    expect(read("ios/App/App/PushPreviewKeysPlugin.swift")).toContain(
      'jsName = "PushPreviewKeys"',
    );
    expect(read(`${androidSources}/PushPreviewKeysPlugin.java`)).toContain(
      '@CapacitorPlugin(name = "PushPreviewKeys")',
    );
  });

  it("rotates keys on the same schedule on iOS and Android", () => {
    const swift = read("ios/App/PushPreview/PushPreviewKeyring.swift");
    const java = read(`${androidSources}/PushPreviewKeyring.java`);
    expect(days(swift, "rotationInterval")).toBe(30);
    expect(days(java, "ROTATION_INTERVAL_MS")).toBe(30);
    // Must outlast the backend's PUSH_TTL_SECONDS (1 day): no push sealed for
    // a retired key may arrive after that key is deleted.
    expect(days(swift, "gracePeriod")).toBe(2);
    expect(days(java, "GRACE_PERIOD_MS")).toBe(2);
  });

  it("words the missing-subject placeholder like the web app everywhere", () => {
    // A preview with an empty subject must not render an empty banner body.
    const noSubject = (locale: string): string =>
      (JSON.parse(read(`public/locales/common/${locale}.json`)) as Record<string, string>)[
        "No subject"
      ];
    const en = noSubject("en-US");
    const fr = noSubject("fr-FR");
    expect(read("ios/App/App/en.lproj/Localizable.strings")).toContain(
      `"NO_SUBJECT" = "${en}";`,
    );
    expect(read("ios/App/App/fr.lproj/Localizable.strings")).toContain(
      `"NO_SUBJECT" = "${fr}";`,
    );
    expect(read("android/app/src/main/res/values/strings.xml")).toContain(
      `<string name="new_message_no_subject">${en}</string>`,
    );
    expect(read("android/app/src/main/res/values-fr/strings.xml")).toContain(
      `<string name="new_message_no_subject">${fr}</string>`,
    );
    expect(read("public/sw.js")).toContain(`IS_FRENCH ? "${fr}" : "${en}"`);
  });

  describe("iOS", () => {
    const project = read("ios/App/App.xcodeproj/project.pbxproj");

    it("registers the keys plugin on the bridge", () => {
      // App-local plugins are not auto-discovered by Capacitor.
      expect(read("ios/App/App/MainViewController.swift")).toContain(
        "registerPluginInstance(PushPreviewKeysPlugin())",
      );
    });

    it("builds and embeds the Notification Service Extension", () => {
      expect(project).toContain('productType = "com.apple.product-type.app-extension"');
      expect(project).toContain("NotificationService.appex in Embed Foundation Extensions");
      expect(read("ios/App/NotificationService/Info.plist")).toContain(
        "com.apple.usernotifications.service",
      );
    });

    it("compiles the shared keyring and crypto into both targets", () => {
      for (const file of ["PushPreviewCrypto.swift", "PushPreviewKeyring.swift"]) {
        expect(project.split(`/* ${file} in Sources */ = {isa = PBXBuildFile`)).toHaveLength(3);
      }
    });

    it("registers the hidden-preview placeholder for the APNs category", () => {
      // Pushes carry aps.category = APNS_CATEGORY ("NEW_MESSAGE", asserted in
      // test_push_preview.py); without the category, a lock screen hiding
      // previews shows a bare "Notification".
      const delegate = read("ios/App/App/AppDelegate.swift");
      expect(delegate).toContain('newMessageCategory = "NEW_MESSAGE"');
      expect(delegate).toContain("hiddenPreviewsBodyPlaceholder:");
      expect(delegate).toContain("AppDelegate.registerNotificationCategories()");
    });

    it("shares one Keychain group between the app and the extension", () => {
      for (const path of [
        "ios/App/App/App.entitlements",
        "ios/App/NotificationService/NotificationService.entitlements",
      ]) {
        expect(read(path)).toContain("<string>$(PUSH_PREVIEW_KEYCHAIN_GROUP)</string>");
      }
      for (const path of ["ios/App/App/Info.plist", "ios/App/NotificationService/Info.plist"]) {
        expect(read(path)).toMatch(
          /<key>PushPreviewKeychainGroup<\/key>\s*<string>\$\(PUSH_PREVIEW_KEYCHAIN_GROUP\)<\/string>/,
        );
      }
    });
  });

  describe("Android", () => {
    const manifest = read("android/app/src/main/AndroidManifest.xml");

    it("registers the keys plugin before the bridge is created", () => {
      const activity = read(`${androidSources}/MainActivity.java`);
      const registration = activity.indexOf("registerPlugin(PushPreviewKeysPlugin.class)");
      expect(registration).toBeGreaterThan(-1);
      expect(registration).toBeLessThan(activity.indexOf("super.onCreate(savedInstanceState)"));
    });

    it("routes FCM messages to the preview service instead of the plugin's", () => {
      // Only one service may receive MESSAGING_EVENT; ours extends the plugin's.
      expect(manifest).toMatch(
        /android:name="com\.capacitorjs\.plugins\.pushnotifications\.MessagingService"\s*tools:node="remove"/,
      );
      expect(manifest).toMatch(
        /android:name="\.PushPreviewMessagingService"[\s\S]*?com\.google\.firebase\.MESSAGING_EVENT/,
      );
      expect(read(`${androidSources}/PushPreviewMessagingService.java`)).toContain(
        "extends MessagingService",
      );
    });

    it("gives previews a generic public version for hidden lock screens", () => {
      const service = read(`${androidSources}/PushPreviewMessagingService.java`);
      expect(service).toContain("setVisibility(NotificationCompat.VISIBILITY_PRIVATE)");
      expect(service).toContain(".setPublicVersion(");
    });

    it("renders previews on the app's notification channel", () => {
      expect(read(`${androidSources}/PushPreviewMessagingService.java`)).toContain(
        `CHANNEL_ID = "${ANDROID_NOTIFICATION_CHANNEL_ID}"`,
      );
    });
  });
});

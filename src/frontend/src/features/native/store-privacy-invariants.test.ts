/**
 * Tripwires for what the stores check about privacy and export compliance.
 *
 * None of it shows while developing: App Store Connect rejects an upload whose
 * privacy manifest misses a required-reason API (ITMS-91053), a missing export
 * compliance answer only adds a question at every upload, and the OTA
 * updater's statistics silently reach a third-party server. These tests pin
 * the files themselves. Whether the declarations still match what the app
 * really does is checked on the archive (Xcode's privacy report) before a
 * store release.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (relativePath: string): string =>
  readFileSync(resolve(frontendRoot, relativePath), "utf8");

describe("store privacy invariants", () => {
  describe("iOS", () => {
    const project = read("ios/App/App.xcodeproj/project.pbxproj");
    const manifest = read("ios/App/App/PrivacyInfo.xcprivacy");

    it("bundles the privacy manifest in the app", () => {
      const appResources = /504EC3021FED79650016851F \/\* Resources \*\/ = \{[\s\S]*?\n\t\t\};/.exec(
        project,
      )?.[0];
      expect(appResources).toContain("/* PrivacyInfo.xcprivacy in Resources */");
    });

    it.each([
      // PushPreviewKeysPlugin and the OTA updater.
      ["NSPrivacyAccessedAPICategoryUserDefaults", "CA92.1"],
      // The Filesystem plugin and the OTA updater.
      ["NSPrivacyAccessedAPICategoryFileTimestamp", "C617.1"],
      // The OTA updater.
      ["NSPrivacyAccessedAPICategoryDiskSpace", "E174.1"],
    ])("declares the %s API with reason %s", (category, reason) => {
      expect(manifest).toMatch(
        new RegExp(
          `<string>${category}</string>\\s*<key>NSPrivacyAccessedAPITypeReasons</key>\\s*<array>\\s*<string>${reason}</string>`,
        ),
      );
    });

    it("declares no tracking", () => {
      expect(manifest).toMatch(/<key>NSPrivacyTracking<\/key>\s*<false\/>/);
      expect(manifest).not.toMatch(/<key>NSPrivacyCollectedDataTypeTracking<\/key>\s*<true\/>/);
    });

    it("answers the export compliance question", () => {
      expect(read("ios/App/App/Info.plist")).toMatch(
        /<key>ITSAppUsesNonExemptEncryption<\/key>\s*<false\/>/,
      );
    });
  });

  it("keeps the OTA updater's statistics off", () => {
    expect(read("capacitor.config.ts")).toMatch(/\bstatsUrl:\s*"",/);
  });
});

/**
 * Tripwires for what the stores check about privacy and export compliance.
 *
 * None of it shows while developing: App Store Connect rejects an upload whose
 * privacy manifest misses a required-reason API (ITMS-91053), a missing export
 * compliance answer only adds a question at every upload, a missing purpose
 * string only kills the app once a user reaches that resource, and the OTA
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

    describe.each([
      // The file inputs of the WebView offer to take a photo or a video.
      ["NSCameraUsageDescription"],
      // Taking a video from those same inputs records sound.
      ["NSMicrophoneUsageDescription"],
      // The share sheet of a downloaded attachment offers to save an image.
      ["NSPhotoLibraryAddUsageDescription"],
    ])("%s", (key) => {
      it("has a purpose string", () => {
        expect(read("ios/App/App/Info.plist")).toMatch(
          new RegExp(`<key>${key}</key>\\s*<string>[^<]+</string>`),
        );
      });

      it("has a French purpose string", () => {
        expect(read("ios/App/App/fr.lproj/InfoPlist.strings")).toMatch(
          new RegExp(`"${key}" = "[^"]+";`),
        );
      });
    });

    it("bundles the French purpose strings in the app", () => {
      const appResources = /504EC3021FED79650016851F \/\* Resources \*\/ = \{[\s\S]*?\n\t\t\};/.exec(
        project,
      )?.[0];
      expect(appResources).toContain("/* InfoPlist.strings in Resources */");
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

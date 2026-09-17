import { Capacitor } from "@capacitor/core";

/**
 * Check whether the app is running inside a Capacitor native shell
 * (iOS or Android). Returns false in a regular web browser.
 *
 * Dev-only escape hatch: `localStorage.DEV_FAKE_NATIVE = "1"` forces the
 * native UI paths (mobile toolbar, `.native` chrome…) in a desktop browser so
 * they can be inspected with devtools. Compiled out of production builds.
 */
export const isNativePlatform = (): boolean =>
  Capacitor.isNativePlatform() ||
  (import.meta.env.DEV && localStorage.getItem("DEV_FAKE_NATIVE") === "1");

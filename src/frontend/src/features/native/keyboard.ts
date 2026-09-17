/**
 * Native (Capacitor) keyboard chrome tweaks for the iOS / Android shells.
 */
import { Keyboard } from "@capacitor/keyboard";

import { isNativePlatform } from "./platform";

/**
 * Hide the iOS form accessory bar (the ▲▼ / done strip WKWebView adds above
 * the keyboard): the app pins its own formatting toolbar right above the
 * keyboard, so the two would stack. The API is iPhone-only and rejects with
 * "unimplemented" elsewhere — swallowed, as Android never shows such a bar.
 */
export const hideKeyboardAccessoryBar = async () => {
  if (!isNativePlatform()) return;
  try {
    await Keyboard.setAccessoryBarVisible({ isVisible: false });
  } catch {
    // Not available on this platform (Android / web): nothing to hide.
  }
};

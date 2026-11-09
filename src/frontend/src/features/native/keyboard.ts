/**
 * Native (Capacitor) keyboard tweaks for the iOS / Android shells.
 */
import { Capacitor } from "@capacitor/core";
import { Keyboard } from "@capacitor/keyboard";

import { isNativePlatform } from "./platform";

const KEYBOARD_FIELD_SELECTOR = [
  'input:not([type="button"], [type="checkbox"], [type="color"], [type="file"], [type="hidden"], [type="image"], [type="radio"], [type="range"], [type="reset"], [type="submit"])',
  "select",
  "textarea",
  '[contenteditable]:not([contenteditable="false"])',
].join(", ");

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

/**
 * Brings the keyboard back when the user returns from a surface they opened
 * while typing (in-app browser of the Drive picker, system file chooser…).
 * iOS reopens it by itself; Android gives the focus back to the field but
 * leaves the keyboard down, under a composer toolbar that only makes sense
 * above it.
 *
 * To call right before leaving: it waits for the webview to lose the system
 * focus, then to get it back, and only reopens if the same field still holds
 * the focus.
 */
export const restoreKeyboardOnReturn = (): void => {
  if (Capacitor.getPlatform() !== "android") return;
  const field = document.activeElement;
  if (!field?.matches(KEYBOARD_FIELD_SELECTOR)) return;

  const onReturn = () => {
    if (document.activeElement === field) void Keyboard.show().catch(() => {});
  };
  window.addEventListener(
    "blur",
    () => window.addEventListener("focus", onReturn, { once: true }),
    { once: true },
  );
};

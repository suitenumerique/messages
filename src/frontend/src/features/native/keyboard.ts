/**
 * Native (Capacitor) keyboard tweaks for the iOS / Android shells.
 */
import { Capacitor, registerPlugin } from "@capacitor/core";
import { Keyboard } from "@capacitor/keyboard";

import { COMPOSER_PORTALED_PIECES_SELECTOR } from "@/features/blocknote/toolbar-focus";

/** App-local plugin (ios/App/App/KeyboardAccessoryPlugin.swift), iOS only. */
type KeyboardAccessoryPlugin = {
  reload: () => Promise<void>;
};

const KeyboardAccessory = registerPlugin<KeyboardAccessoryPlugin>("KeyboardAccessory");

const KEYBOARD_FIELD_SELECTOR = [
  'input:not([type="button"], [type="checkbox"], [type="color"], [type="file"], [type="hidden"], [type="image"], [type="radio"], [type="range"], [type="reset"], [type="submit"])',
  "select",
  "textarea",
  '[contenteditable]:not([contenteditable="false"])',
].join(", ");

const COMPOSER_SELECTOR = `.bn-container, ${COMPOSER_PORTALED_PIECES_SELECTOR}`;

const needsAccessoryBar = (element: Element | null) =>
  !!element?.matches(KEYBOARD_FIELD_SELECTOR) && !element.closest(COMPOSER_SELECTOR);

/**
 * Keep the iOS form accessory bar (the ▲▼ / done strip WKWebView adds above
 * the keyboard) on regular fields only: BlockNote fields pin their own
 * formatting toolbar right above the keyboard, so the two would stack.
 *
 * The bar stays hidden at rest, so that opening a composer never flashes it.
 *
 * @returns a function that stops following the focus.
 */
export const syncKeyboardAccessoryBarWithFocus = (): (() => void) => {
  if (Capacitor.getPlatform() !== "ios") return () => {};

  let isVisible = false;
  // Each change must reach the native side whole and in order: the reload has
  // to see the visibility set just before it.
  let lastChange: Promise<void> = Keyboard.setAccessoryBarVisible({ isVisible }).catch(() => {});

  const sync = () => {
    const nextIsVisible = needsAccessoryBar(document.activeElement);
    if (nextIsVisible === isVisible) return;
    isVisible = nextIsVisible;
    lastChange = lastChange
      .then(() => Keyboard.setAccessoryBarVisible({ isVisible: nextIsVisible }))
      .then(() => KeyboardAccessory.reload())
      // An OTA bundle can run on an older shell without the reload plugin: the
      // bar then only updates the next time a keyboard opens.
      .catch(() => {});
  };

  // Deferred because the active element is still unsettled during a focusout,
  // and a focus move between two fields must not hide the bar in between.
  const onFocusChange = () => {
    setTimeout(sync, 0);
  };

  document.addEventListener("focusin", onFocusChange);
  document.addEventListener("focusout", onFocusChange);
  return () => {
    document.removeEventListener("focusin", onFocusChange);
    document.removeEventListener("focusout", onFocusChange);
  };
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

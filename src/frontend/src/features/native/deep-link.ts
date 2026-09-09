/**
 * Single, app-lifetime entry point for the Capacitor `appUrlOpen` events.
 *
 * The native plugin notifies that event with `retainUntilConsumed = true`
 * (@capacitor/app AppPlugin.java): a deep link arriving while no JS listener
 * is registered is *retained* and replayed to the next listener that
 * subscribes. A flow subscribing per attempt therefore gets served the
 * *previous* attempt's callback — which is how a login whose JS context was
 * destroyed mid-flight (see auth.ts) stayed broken attempt after attempt,
 * until the app was killed.
 *
 * So the listener is registered once, at boot, and routes every link: a flow
 * in flight consumes its own callback, and anything arriving outside one goes
 * to the fallback, which decides to resume it or drop it. Nothing is ever left
 * pending on the JS side, so no link can leak into a later attempt.
 */
import { App } from "@capacitor/app";

import { isNativePlatform } from "./platform";

type DeepLinkHandler = (url: string) => void;

let fallbackHandler: DeepLinkHandler | null = null;
let activeHandler: DeepLinkHandler | null = null;
let listening = false;

/**
 * Register the app-lifetime `appUrlOpen` listener. Call it as early as
 * possible at boot: any link retained by the native layer (the app was not
 * running, or was reloaded, when it arrived) is replayed the moment this
 * subscribes, and the fallback is what turns that replay into a resumed flow
 * instead of a stale callback poisoning the next attempt.
 *
 * @param fallback Handles links arriving outside an in-flight flow
 */
export const initNativeDeepLinks = (fallback: DeepLinkHandler): void => {
  fallbackHandler = fallback;
  if (listening || !isNativePlatform()) {
    return;
  }
  listening = true;
  void App.addListener("appUrlOpen", (event) => {
    (activeHandler ?? fallbackHandler)?.(event.url);
  });
};

/**
 * Route incoming deep links to `handler` until the returned function is
 * called. Used by a flow that opened the system browser and awaits its own
 * callback; releasing hands routing back to the boot fallback.
 */
export const captureDeepLinks = (handler: DeepLinkHandler): (() => void) => {
  activeHandler = handler;
  return () => {
    if (activeHandler === handler) {
      activeHandler = null;
    }
  };
};

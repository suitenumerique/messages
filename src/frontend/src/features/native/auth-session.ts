import { Browser } from "@capacitor/browser";
import { Capacitor, registerPlugin } from "@capacitor/core";

import { captureDeepLinks } from "./deep-link";
import { holdOtaInstall, releaseOtaInstall } from "./ota";

/**
 * Local iOS plugin exposing ASWebAuthenticationSession
 * (see ios/App/App/WebAuthSessionPlugin.swift). The default Capacitor
 * Browser plugin uses SFSafariViewController on iOS, whose cookie store is
 * isolated from Safari and would break cross-app SSO.
 */
type WebAuthSessionPlugin = {
  start(options: {
    url: string;
    callbackScheme: string;
  }): Promise<{ callbackUrl: string }>;
};

const WebAuthSession = registerPlugin<WebAuthSessionPlugin>("WebAuthSession");

const CANCELLED_REJECT_DELAY = 1000;

/**
 * Await the deep link closing an Android Custom Tab flow.
 *
 * The callback is routed by the app-lifetime dispatcher (see deep-link.ts)
 * rather than a listener of our own: subscribing per attempt would collect
 * whatever link the native layer retained from an earlier one.
 */
const runAndroidAuthSession = (
  url: string,
  callbackScheme: string,
): Promise<string> =>
  new Promise<string>((resolve, reject) => {
    let settled = false;
    const cleanups: (() => void)[] = [];

    const settle = () => {
      settled = true;
      cleanups.forEach((release) => release());
    };

    cleanups.push(
      captureDeepLinks((callbackUrl) => {
        if (!callbackUrl.startsWith(`${callbackScheme}://`)) return;
        settle();
        resolve(callbackUrl);
      }),
    );

    void Browser.addListener("browserFinished", () => {
      // On a successful login the Custom Tab may close itself right before
      // the deep link reaches the app: delay the rejection so a settled
      // promise wins over a spurious "cancelled".
      setTimeout(() => {
        if (settled) return;
        settle();
        reject(new Error("Authentication was cancelled."));
      }, CANCELLED_REJECT_DELAY);
    }).then((handle) => {
      const release = () => void handle.remove();
      // The promise may already have settled while this registration was in
      // flight — releasing right away is what keeps the listener from
      // outliving the flow that created it.
      if (settled) release();
      else cleanups.push(release);
    });

    // A rejected open (e.g. no Custom Tabs provider) would otherwise leave
    // the promise pending forever with the listeners still registered.
    Browser.open({ url }).catch((error: unknown) => {
      settle();
      reject(error instanceof Error ? error : new Error(String(error)));
    });
  });

/**
 * Open the given URL in the system browser (ASWebAuthenticationSession on
 * iOS, Chrome Custom Tabs on Android) and resolve with the deep-link URL
 * the authentication flow redirected to.
 *
 * Holds back any staged OTA bundle for the duration: the system browser
 * backgrounds the app, which is exactly when the native updater installs a
 * staged bundle and reloads the WebView — killing the flow mid-air.
 *
 * @param url The authentication URL to open
 * @param callbackScheme The deep-link scheme ending the flow
 * @returns The full callback URL (e.g. `scheme://auth?token=...`)
 */
export const openAuthSession = async (
  url: string,
  callbackScheme: string,
): Promise<string> => {
  await holdOtaInstall();
  try {
    if (Capacitor.getPlatform() === "ios") {
      const { callbackUrl } = await WebAuthSession.start({
        url,
        callbackScheme,
      });
      return callbackUrl;
    }
    return await runAndroidAuthSession(url, callbackScheme);
  } finally {
    void releaseOtaInstall();
  }
};

import { App } from "@capacitor/app";
import { Browser } from "@capacitor/browser";
import { Capacitor, registerPlugin } from "@capacitor/core";
import type { PluginListenerHandle } from "@capacitor/core";

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
 * Open the given URL in the system browser (ASWebAuthenticationSession on
 * iOS, Chrome Custom Tabs on Android) and resolve with the deep-link URL
 * the authentication flow redirected to.
 *
 * @param url The authentication URL to open
 * @param callbackScheme The deep-link scheme ending the flow
 * @returns The full callback URL (e.g. `scheme://auth?token=...`)
 */
export const openAuthSession = async (
  url: string,
  callbackScheme: string,
): Promise<string> => {
  if (Capacitor.getPlatform() === "ios") {
    const { callbackUrl } = await WebAuthSession.start({ url, callbackScheme });
    return callbackUrl;
  }

  return new Promise<string>((resolve, reject) => {
    const handles: PluginListenerHandle[] = [];
    const cleanup = () => {
      handles.forEach((handle) => void handle.remove());
    };

    void App.addListener("appUrlOpen", (event) => {
      if (!event.url.startsWith(`${callbackScheme}://`)) return;
      cleanup();
      resolve(event.url);
    }).then((handle) => handles.push(handle));

    void Browser.addListener("browserFinished", () => {
      // On a successful login the Custom Tab may close itself right before
      // the deep link reaches the app: delay the rejection so a settled
      // promise wins over a spurious "cancelled".
      setTimeout(() => {
        cleanup();
        reject(new Error("Authentication was cancelled."));
      }, CANCELLED_REJECT_DELAY);
    }).then((handle) => handles.push(handle));

    // A rejected open (e.g. no Custom Tabs provider) would otherwise leave
    // the promise pending forever with the listeners still registered.
    Browser.open({ url }).catch((error: unknown) => {
      cleanup();
      reject(error instanceof Error ? error : new Error(String(error)));
    });
  });
};

import { Browser } from "@capacitor/browser";
import type { PluginListenerHandle } from "@capacitor/core";
import {
  ClientMessageType,
  type Item,
  type PickerResult,
  type SDKRelayEvent,
} from "@gouvfr-lasuite/drive-sdk";

/**
 * Native counterpart of the Drive SDK's `openPicker`, for the Capacitor
 * shell.
 *
 * The SDK opens the picker with `window.open(popup)` and polls a relay
 * endpoint from the opener. Inside the shell that popup lands in the system
 * browser: the WebView is backgrounded (iOS suspends its timers, freezing the
 * poll until the user comes back by hand) and the SDK's `popup.close()` has
 * no effect on another app. This reimplements the same token/relay flow with
 * the in-app browser instead — the app stays foreground so the poll keeps
 * running, and `Browser.close()` dismisses the picker as soon as a selection
 * comes through.
 */

const POLL_INTERVAL_MS = 500;

const randomToken = (length = 32): string => {
  const alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("");
};

const toPickerResult = (event: SDKRelayEvent): PickerResult => {
  if (event.type === ClientMessageType.ITEMS_SELECTED) {
    return {
      type: "picked",
      items: (event.data as { items: Item[] }).items,
    };
  }
  return { type: "cancelled" };
};

export const openNativeDrivePicker = (config: {
  url: string;
  apiUrl: string;
}): Promise<PickerResult> => {
  const token = randomToken();

  const fetchRelayEvent = async (): Promise<SDKRelayEvent | null> => {
    const response = await fetch(`${config.apiUrl}/sdk-relay/events/${token}/`);
    const event = (await response.json()) as SDKRelayEvent | null;
    return event?.type ? event : null;
  };

  return new Promise<PickerResult>((resolve) => {
    let settled = false;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    const handles: PluginListenerHandle[] = [];

    const settle = (result: PickerResult, closeBrowser: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(pollTimer);
      handles.forEach((handle) => void handle.remove());
      if (closeBrowser) void Browser.close().catch(() => undefined);
      resolve(result);
    };

    const poll = async () => {
      try {
        const event = await fetchRelayEvent();
        if (event) {
          settle(toPickerResult(event), true);
          return;
        }
      } catch {
        // Transient relay error: keep polling, the browser is still open.
      }
      if (!settled) pollTimer = setTimeout(() => void poll(), POLL_INTERVAL_MS);
    };

    void Browser.addListener("browserFinished", () => {
      // The user dismissed the in-app browser themselves. A selection posted
      // right before the tab closed may not have been polled yet: check the
      // relay one last time before concluding to a cancellation.
      fetchRelayEvent()
        .then((event) =>
          settle(event ? toPickerResult(event) : { type: "cancelled" }, false),
        )
        .catch(() => settle({ type: "cancelled" }, false));
    }).then((handle) => handles.push(handle));

    Browser.open({ url: `${config.url}?token=${token}` })
      .then(() => void poll())
      .catch(() => settle({ type: "cancelled" }, false));
  });
};

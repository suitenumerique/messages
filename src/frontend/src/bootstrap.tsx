import { createRoot } from "react-dom/client";
import { createRouter, parseSearchWith, RouterProvider, stringifySearchWith } from "@tanstack/react-router";

import { routeTree } from "./routes.gen";
import { isNativePlatform } from "./features/native/platform";
import { configRetrieve, configRetrieveResponse, getConfigRetrieveQueryKey } from "@/features/api/gen";
import { queryClient } from "@/features/api/query-client";
import { resolveConfig } from "@/features/config/resolve";
import { initI18n } from "@/features/i18n/initI18n";
import { installThemeFavicons } from "@/features/providers/theme-favicons";
import { initSentry } from "@/features/sentry";
import { handle } from '@/features/utils/errors';
import { hideKeyboardAccessoryBar } from "./features/native/keyboard";
import { checkAndApplyOtaUpdate, notifyOtaAppReady } from "./features/native/ota";
import { listenForNativePushTaps } from "./features/native/push";

// Tag the document on the Capacitor native app so the stylesheet can opt into
// mobile-only chrome (compact header, floating bottom bars) without each
// component re-deriving the platform.
if (isNativePlatform()) {
  document.documentElement.classList.add("native");

  // The composer pins its own formatting toolbar above the keyboard; iOS's
  // form accessory bar would stack under it, so hide it once and for all.
  void hideKeyboardAccessoryBar();

  // Pinch-zooming the whole shell makes it feel like a website, not an app, so
  // lock the viewport scale — but only here: on the web the index.html viewport
  // stays zoomable (accessibility, WCAG 1.4.4).
  document
    .querySelector('meta[name="viewport"]')
    ?.setAttribute(
      "content",
      "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover",
    );
}

// Default TSR encoding JSON-wraps every search value (`?key=1` → `?key=%221%22`).
// The rest of the app builds URLs via `URLSearchParams.toString()` and the
// backend expects plain values, so we plug identity parsers to keep both sides
// aligned — values stay as raw strings on the way out and on the way back.
const router = createRouter({
  routeTree,
  scrollRestoration: false,
  defaultPreload: false,
  parseSearch: parseSearchWith((value) => value),
  stringifySearch: stringifySearchWith((value) => (value == null ? "" : String(value))),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// Notification taps deep-link into the thread the push points at, through the
// router history (an in-app navigation, not a WebView reload). Registered
// before any await so the tap that cold-started the app is not missed.
listenForNativePushTaps((url) => router.history.push(url));

/**
 * Fetch the backend configuration then initialize everything that must be
 * ready before the first React render: Sentry, i18n and the theme favicons.
 * The response also primes the React Query cache so `ConfigProvider` reads
 * it without a second fetch.
 */
export const bootstrap = async () => {
  const container = document.getElementById("root");
  if (!container) throw new Error("#root element not found in index.html");

  try {
    let response: configRetrieveResponse | undefined;
    try {
      response = await configRetrieve();
    } catch (error) {
      // The app still boots on deprecated env vars and hardcoded defaults.
      // The cache is intentionally left unprimed so ConfigProvider retries
      // the fetch on mount and the React tree self-heals if the API is back.
      console.error("[config] Failed to fetch the configuration, falling back to build-time defaults.", error);
    }

    const config = resolveConfig(response?.data);
    // Fire-and-forget: a pending update reloads the WebView once downloaded;
    // until then the current bundle renders normally.
    void checkAndApplyOtaUpdate(config.MOBILE_OTA_MANIFEST_URL);
    initSentry(config);
    initI18n(config);
    installThemeFavicons(config.THEME_CONFIG.theme);
    if (response) {
      queryClient.setQueryData(getConfigRetrieveQueryKey(), response);
    }

    createRoot(container).render(<RouterProvider router={router} />);
  } catch (error) {
    // Last-resort safety net: this runs before the React ErrorBoundary exists.
    handle(error);
    container.innerHTML =
      "<p>Something went wrong while starting the application. Please try again later.</p>";
    return;
  }

  void notifyOtaAppReady();
};

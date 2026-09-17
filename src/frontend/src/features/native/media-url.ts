import { Capacitor, type CapacitorGlobal } from "@capacitor/core";

import { isNativePlatform } from "./platform";

// The bridge keeps these private in native-bridge.js (`createProxyUrl`, flagged
// "TODO: export as Cap function"). Both native sides match on the same path:
// `Bridge.CAPACITOR_HTTP_INTERCEPTOR_START` (Android) and
// `CapacitorBridge.httpInterceptorStartIdentifier` (iOS).
const HTTP_INTERCEPTOR_PATH = "/_capacitor_http_interceptor_";
const HTTP_INTERCEPTOR_URL_PARAM = "u";

// `getServerUrl` is installed by native-bridge.js but left out of the public
// `CapacitorGlobal` type; absent on the web runtime.
type CapacitorWithServerUrl = CapacitorGlobal & { getServerUrl?: () => string };

/**
 * Origin the WebView content is served from inside the Capacitor shell
 * (`capacitor://localhost` on iOS, `https://localhost` on Android), even when
 * the page itself comes from the Vite dev server. Empty outside the shell.
 */
export const getNativeServerUrl = (): string =>
  (Capacitor as CapacitorWithServerUrl).getServerUrl?.() ?? "";

/**
 * Rewrite an API media URL so a WebView subresource (`<img>`, `<video>`,
 * `<audio>`) reaches the backend through the native HTTP layer. No-op on the
 * web.
 *
 * `CapacitorHttp` only patches `fetch`/`XHR`: subresources are loaded by the
 * WebView's own network stack, from the shell origin — cross-site with the
 * API. The session cookie is `SameSite=Lax` (and WKWebView blocks third-party
 * cookies outright), so those loads carry no session and get a 401. Invisible
 * in dev, where the dev server and the backend both live on `localhost` and
 * are therefore same-site.
 *
 * The bridge already routes its own GET fetches through a same-origin proxy
 * URL that the native side intercepts (`shouldInterceptRequest` on Android,
 * the `WKURLSchemeHandler` on iOS) and serves from the native HTTP stack:
 * native cookie jar, streamed, nothing crossing the JS bridge. Pointing the
 * subresource at that same URL gives it the session for free.
 */
export const toNativeMediaUrl = (url: string): string => {
  if (!isNativePlatform()) return url;
  const serverUrl = getNativeServerUrl();
  if (!serverUrl) return url;

  const proxyUrl = new URL(serverUrl);
  proxyUrl.pathname = HTTP_INTERCEPTOR_PATH;
  proxyUrl.search = "";
  proxyUrl.searchParams.set(HTTP_INTERCEPTOR_URL_PARAM, url);
  return proxyUrl.toString();
};

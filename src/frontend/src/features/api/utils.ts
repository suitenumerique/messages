import { getNativeCsrfToken } from "@/features/native/csrf";
import { isNativePlatform } from "@/features/native/platform";
import { getWebCsrfToken } from "./csrf";

export const errorCauses = async (response: Response, data?: unknown) => {
  const errorsBody = (await response.json()) as Record<
    string,
    string | string[]
  > | null;

  const causes = errorsBody
    ? Object.entries(errorsBody)
        .map(([, value]) => value)
        .flat()
    : undefined;

  return {
    status: response.status,
    cause: causes,
    data,
  };
};

export const isJson = (str: string) => {
  try {
    JSON.parse(str);
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
  } catch (e) {
    return false;
  }
  return true;
};

export function getApiOrigin() {
  return import.meta.env.NEXT_PUBLIC_API_ORIGIN ||
    (typeof window !== "undefined" ? window.location.origin : "");
}

/**
 * Origin to use when building user-facing absolute links (shareable deep
 * links…). Inside the Capacitor shell `window.location.origin` is the local
 * WebView origin (https://localhost), meaningless outside the app — use the
 * deployed origin instead: in production the API origin also serves the web
 * app (the same assumption `getApiOrigin` relies on for its web fallback).
 */
export function getAppOrigin() {
  return isNativePlatform() ? getApiOrigin() : window.location.origin;
}

/**
 * Build the request url from the context url and the base url
 *
 */
export function getRequestUrl(pathname: string, params?: Record<string, string>): string {

  const requestUrl = new URL(`${getApiOrigin()}${pathname}`);

  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      requestUrl.searchParams.set(key, value);
    });
  }

  return requestUrl.toString();
};

export const getHeaders = (headers: HeadersInit = {}, isMultipartFormData: boolean = false): HeadersInit => {
  const csrfToken = getCSRFToken();
  return {
    // If the request is a multipart/form-data, don't set the Content-Type header
    // as the browser will set it automatically with correct boundary
    ...(isMultipartFormData ? {} : { 'Content-Type': 'application/json' }),
    ...headers,
    ...(csrfToken && { "X-CSRFToken": csrfToken }),
    // Django's CSRF middleware rejects HTTPS requests carrying neither Origin
    // nor Referer ("Referer checking failed - no Referer"). The CapacitorHttp
    // native client sends no browser headers, so declare our origin explicitly:
    // it matches the request host, so Django's same-origin check passes without
    // any server-side allowlisting. Browsers forbid setting Origin — and the
    // Capacitor bridge's patched fetch strips it the same way — so it only
    // reaches the wire through nativeFetch, which mutations use in the shell.
    ...(isNativePlatform() && { Origin: getApiOrigin() }),
  };
};

/**
* Retrieves the CSRF token to echo in the X-CSRFToken header.
*
* With CSRF_USE_SESSIONS the secret lives in the server-side session and there
* is no readable `csrftoken` cookie: the token is delivered over an
* authenticated channel and cached in memory — `/users/me/` for the web app,
* the session exchange for the Capacitor shell.
*
* @returns {string|undefined} The CSRF token if known, or undefined otherwise.
*/
export function getCSRFToken() {
  if (isNativePlatform()) {
    return getNativeCsrfToken();
  }
  return getWebCsrfToken();
}

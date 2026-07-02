/**
 * CSRF token storage for the native shell.
 *
 * On native platforms the session cookies live in the native HTTP layer
 * (CapacitorHttp), so the WebView cannot read the `csrftoken` cookie from
 * `document.cookie`. The token returned by the mobile session exchange is
 * persisted here instead and injected by `getCSRFToken()`.
 */

const NATIVE_CSRF_TOKEN_KEY = "messages_native-csrf-token";

/**
 * Persist the CSRF token returned by the mobile session exchange,
 * or clear it when passing null (logout).
 */
export const setNativeCsrfToken = (token: string | null): void => {
  if (token === null) {
    localStorage.removeItem(NATIVE_CSRF_TOKEN_KEY);
    return;
  }
  localStorage.setItem(NATIVE_CSRF_TOKEN_KEY, token);
};

/**
 * Retrieve the CSRF token stored by the mobile session exchange.
 */
export const getNativeCsrfToken = (): string | undefined =>
  localStorage.getItem(NATIVE_CSRF_TOKEN_KEY) ?? undefined;

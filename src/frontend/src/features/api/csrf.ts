/**
 * In-memory CSRF token for the web app.
 *
 * With `CSRF_USE_SESSIONS` the backend keeps the CSRF secret in the server-side
 * session and no longer exposes a readable `csrftoken` cookie. The token is
 * delivered over the authenticated `/users/me/` response, cached here, and
 * injected as the `X-CSRFToken` header by `getCSRFToken()`. Kept in memory (not
 * a cookie) so it is never auto-attached cross-site and is dropped on reload.
 */

let webCsrfToken: string | undefined;

export const setWebCsrfToken = (token: string | undefined): void => {
  webCsrfToken = token;
};

export const getWebCsrfToken = (): string | undefined => webCsrfToken;

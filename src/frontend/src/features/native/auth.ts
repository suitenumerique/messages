import { CapacitorCookies } from "@capacitor/core";

import { getRequestUrl } from "@/features/api/utils";

import { openAuthSession } from "./auth-session";
import { getNativeCsrfToken, setNativeCsrfToken } from "./csrf";
import { computeCodeChallenge, generateCodeVerifier } from "./pkce";

/**
 * Deep-link scheme ending the mobile OIDC flow. The backend allowlists it
 * through MOBILE_AUTH_CALLBACK_SCHEMES.
 */
const AUTH_CALLBACK_SCHEME = "stmessages";

type ExchangeResponse = {
  csrf_token: string;
};

/**
 * Run the OIDC login in the system browser and hand the resulting Django
 * session over to the native HTTP layer.
 *
 * The system browser shares the identity provider session cookie across
 * apps, which is what provides cross-app SSO. The backend ends the flow
 * with a deep link carrying a one-time token, exchanged here (with the
 * PKCE verifier) for the session cookie.
 */
export const nativeLogin = async (): Promise<void> => {
  try {
    const scheme = AUTH_CALLBACK_SCHEME;
    const codeVerifier = generateCodeVerifier();
    const codeChallenge = await computeCodeChallenge(codeVerifier);

    const callbackUrl = await openAuthSession(
      getRequestUrl("/api/v1.0/authenticate/", {
        mobile_scheme: scheme,
        code_challenge: codeChallenge,
      }),
      scheme,
    );

    const callbackParams = new URL(callbackUrl).searchParams;
    const token = callbackParams.get("token");
    if (!token) {
      throw new Error(
        `Native login failed: ${callbackParams.get("error") ?? "no token in callback"}`,
      );
    }

    const response = await fetch(getRequestUrl("/api/v1.0/mobile/auth/exchange/"), {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, code_verifier: codeVerifier }),
    });
    if (!response.ok) {
      throw new Error(`Mobile session exchange failed (${response.status}).`);
    }

    const { csrf_token: csrfToken } = (await response.json()) as ExchangeResponse;
    setNativeCsrfToken(csrfToken);
    window.location.reload();
  } catch (error) {
    // A cancelled system-browser sheet lands here too: stay on the login
    // screen instead of crashing the shell.
    console.warn("Native login did not complete:", error);
  }
};

/**
 * End the app session while preserving cross-app SSO.
 *
 * Calling /api/v1.0/logout/ would trigger the RP-initiated IdP logout
 * (id_token_hint) and terminate the cross-app SSO session. The dedicated
 * mobile endpoint flushes only the server-side Django session; the local
 * cookies and CSRF token are then dropped. Never rejects: the reload into
 * the logged-out state must happen even when a step fails.
 */
export const nativeLogout = async (): Promise<void> => {
  try {
    // Invalidate the server-side session first: even if clearing the native
    // cookie jar fails below, the cookie no longer maps to a live session.
    const csrfToken = getNativeCsrfToken();
    await fetch(getRequestUrl("/api/v1.0/mobile/auth/logout/"), {
      method: "POST",
      credentials: "include",
      headers: csrfToken ? { "X-CSRFToken": csrfToken } : undefined,
    });
    await CapacitorCookies.clearAllCookies();
  } catch (error) {
    // Best effort: an orphaned server-side session expires with its TTL.
    console.warn("Native logout did not fully complete:", error);
  }
  setNativeCsrfToken(null);
  window.location.reload();
};

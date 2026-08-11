import { CapacitorCookies } from "@capacitor/core";

import { getRequestUrl } from "@/features/api/utils";

import { openAuthSession } from "./auth-session";
import { getNativeCsrfToken, setNativeCsrfToken } from "./csrf";
import { computeCodeChallenge, generateCodeVerifier } from "./pkce";

/**
 * Deep-link scheme ending the mobile OIDC flow. The backend allowlists it
 * through MOBILE_AUTH_CALLBACK_SCHEMES (a list, so several environments can be
 * served at once).
 *
 * Per-environment (MOBILE_AUTH_SCHEME) so staging and production builds can sit
 * side by side on one device: two installed apps claiming the same scheme would
 * make Android ask the user which one should receive the login callback, in the
 * middle of the auth flow. The same value must reach both native declarations —
 * the Android manifest through a gradle manifestPlaceholder, the iOS Info.plist
 * through the AUTH_CALLBACK_SCHEME build setting — and the default below must
 * stay in sync with theirs; sso-invariants.test.ts pins that wiring.
 */
const AUTH_CALLBACK_SCHEME = import.meta.env.MOBILE_AUTH_SCHEME || "stmessages";

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
 * End the session everywhere: Django AND the identity provider.
 *
 * The RP-initiated logout runs in the system browser, which holds both the
 * Django session cookie (the session handed over at login) and the IdP SSO
 * cookie: the round-trip terminates both, so the next login always stops on
 * the IdP login page and the user can switch accounts — prompt=login alone
 * cannot guarantee it, ProConnect ignores it. Never rejects: the reload into
 * the logged-out state must happen even when a step fails.
 */
export const nativeLogout = async (): Promise<void> => {
  const scheme = AUTH_CALLBACK_SCHEME;
  try {
    // Ends on a scheme://logout deep link once the IdP round-trip flushed
    // the server-side session.
    await openAuthSession(
      getRequestUrl("/api/v1.0/logout/", { mobile_scheme: scheme }),
      scheme,
    );
  } catch (error) {
    // A cancelled sheet or a failed round-trip must not keep the app
    // signed in: the local flush below still ends the app session.
    console.warn("IdP logout did not complete:", error);
  }
  try {
    // Always flush the app-side server session too: the browser round-trip
    // only ends it when the browser still holds the same session cookie —
    // if the browser dropped it, the app session would otherwise survive.
    // Anonymous no-op when the round-trip already ended it.
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

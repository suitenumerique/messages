import { CapacitorCookies } from "@capacitor/core";

import { getRequestUrl } from "@/features/api/utils";
import { APP_STORAGE_PREFIX } from "@/features/config/constants";

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
 * PKCE verifier of the login currently waiting for its deep link.
 *
 * Persisted rather than kept in a closure because the flow outlives its own JS
 * context: the system browser backgrounds the app, and a backgrounded app can
 * be reloaded (a staged OTA bundle installs on background) or killed outright
 * by Android. The verifier is the one value the exchange cannot re-derive, so
 * losing it stranded the login — the callback came back to an app that no
 * longer knew what to do with it.
 */
const PENDING_LOGIN_KEY = `${APP_STORAGE_PREFIX}native-login-pending`;

/**
 * How long a started login may still be completed. Generous: it covers the
 * whole time the user spends on the identity provider (credentials, MFA,
 * consent), not the much shorter life of the token minted at the very end of
 * it (backend MOBILE_AUTH_TOKEN_TTL). Past it the record is a leftover from a
 * flow the user abandoned, and completing it would be a surprise.
 */
const PENDING_LOGIN_TTL_MS = 15 * 60 * 1000;

type PendingLogin = {
  codeVerifier: string;
  startedAt: number;
};

const writePendingLogin = (pending: PendingLogin): void => {
  localStorage.setItem(PENDING_LOGIN_KEY, JSON.stringify(pending));
};

const clearPendingLogin = (): void => {
  localStorage.removeItem(PENDING_LOGIN_KEY);
};

/**
 * Read the login awaiting its callback, or null when there is none (or the
 * record expired, or storage holds something unparseable).
 */
const readPendingLogin = (): PendingLogin | null => {
  const raw = localStorage.getItem(PENDING_LOGIN_KEY);
  if (!raw) {
    return null;
  }
  try {
    const pending = JSON.parse(raw) as Partial<PendingLogin>;
    if (
      typeof pending.codeVerifier !== "string" ||
      typeof pending.startedAt !== "number" ||
      Date.now() - pending.startedAt > PENDING_LOGIN_TTL_MS
    ) {
      clearPendingLogin();
      return null;
    }
    return { codeVerifier: pending.codeVerifier, startedAt: pending.startedAt };
  } catch {
    clearPendingLogin();
    return null;
  }
};

/**
 * Trade the one-time token carried by the callback for the Django session,
 * and reload into the signed-in app.
 *
 * Reads the verifier from storage, not from the caller, so the exchange works
 * the same whether the callback landed in the flow that started it or in a
 * freshly booted context that inherited a retained deep link.
 */
const completeNativeLogin = async (callbackUrl: string): Promise<void> => {
  const pending = readPendingLogin();
  if (!pending) {
    throw new Error("Native login callback without a pending login.");
  }

  const callbackParams = new URL(callbackUrl).searchParams;
  const token = callbackParams.get("token");
  if (!token) {
    throw new Error(
      `Native login failed: ${callbackParams.get("error") ?? "no token in callback"}`,
    );
  }

  // Cleared before the exchange: the token is single-use server-side, so a
  // failed attempt must not leave a record inviting a retry that can only
  // fail again.
  clearPendingLogin();

  const response = await fetch(getRequestUrl("/api/v1.0/mobile/auth/exchange/"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, code_verifier: pending.codeVerifier }),
  });
  if (!response.ok) {
    throw new Error(`Mobile session exchange failed (${response.status}).`);
  }

  const { csrf_token: csrfToken } = (await response.json()) as ExchangeResponse;
  setNativeCsrfToken(csrfToken);
  window.location.reload();
};

/**
 * Handle an auth deep link arriving outside a running login flow — the boot
 * fallback of the deep-link dispatcher (see deep-link.ts).
 *
 * That happens whenever the flow lost its JS context while the user was on the
 * identity provider: the native layer retains the link and replays it here on
 * the next boot, and the pending record is what lets the login finish instead
 * of silently dropping the user back on the home screen. A link with no
 * pending login behind it belongs to no flow of ours and is dropped — leaving
 * it unconsumed is what used to feed it to the *next* attempt.
 */
export const resumeNativeLogin = (url: string): void => {
  if (!url.startsWith(`${AUTH_CALLBACK_SCHEME}://auth`)) {
    return;
  }
  if (!readPendingLogin()) {
    return;
  }
  void completeNativeLogin(url).catch((error: unknown) => {
    console.warn("Native login could not be resumed:", error);
  });
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

    writePendingLogin({ codeVerifier, startedAt: Date.now() });

    const callbackUrl = await openAuthSession(
      getRequestUrl("/api/v1.0/authenticate/", {
        mobile_scheme: scheme,
        code_challenge: codeChallenge,
      }),
      scheme,
    );

    await completeNativeLogin(callbackUrl);
  } catch (error) {
    // A cancelled system-browser sheet lands here too: stay on the login
    // screen instead of crashing the shell.
    clearPendingLogin();
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

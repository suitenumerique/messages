import {
  OIDC_LOGIN_ATTEMPT_KEY,
  OIDC_LOGIN_FAILED_KEY,
  SESSION_EXPIRED_KEY,
} from "../config/constants";

/**
 * One-shot flags coordinating the OIDC login flow across full-page redirects.
 *
 * Every sign-in / sign-out is a hard navigation, so this state can only
 * survive in sessionStorage:
 * - "attempt": an explicit sign-in was initiated; consumed after the OIDC
 *   callback to detect a sign-in that matched no Messages account.
 * - "failed": remembered after such a failed sign-in until a sign-in
 *   succeeds; blocks the silent login from replaying the same failure in a
 *   loop.
 * - "expired": the session died on a 401; shows the "session expired" toast
 *   once the user is back on the login screen.
 */

export const markLoginAttempt = () =>
  sessionStorage.setItem(OIDC_LOGIN_ATTEMPT_KEY, "true");

export const hasPendingLoginAttempt = () =>
  !!sessionStorage.getItem(OIDC_LOGIN_ATTEMPT_KEY);

/** Remove the pending sign-in marker and tell whether it was set. */
export const consumeLoginAttempt = () => {
  const attempted = hasPendingLoginAttempt();
  sessionStorage.removeItem(OIDC_LOGIN_ATTEMPT_KEY);
  return attempted;
};

export const markLoginFailed = () =>
  sessionStorage.setItem(OIDC_LOGIN_FAILED_KEY, "true");

export const clearLoginFailed = () =>
  sessionStorage.removeItem(OIDC_LOGIN_FAILED_KEY);

export const hasLoginFailed = () =>
  !!sessionStorage.getItem(OIDC_LOGIN_FAILED_KEY);

export const markSessionExpired = () =>
  sessionStorage.setItem(SESSION_EXPIRED_KEY, "true");

export const isSessionExpired = () =>
  !!sessionStorage.getItem(SESSION_EXPIRED_KEY);

/** Remove the session-expired marker and tell whether it was set. */
export const consumeSessionExpired = () => {
  const expired = isSessionExpired();
  sessionStorage.removeItem(SESSION_EXPIRED_KEY);
  return expired;
};

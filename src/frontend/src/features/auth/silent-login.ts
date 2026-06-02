import { getRequestUrl } from "../api/utils";
import { SILENT_LOGIN_RETRY_INTERVAL, SILENT_LOGIN_RETRY_KEY } from "../config/constants";

/**
 * Replace the current window location with the silent login URL
 *
 * The silent login URL is the same as the login URL, but with the silent
 * parameter set to true and the returnTo parameter set
 * to the current window location
 */
const silentLogin = () => {
    const FORWARDABLE_OIDC_PARAMS = ['idp_hint', 'login_hint']
    const searchParams = new URLSearchParams(window.location.search);
  window.location.replace(getRequestUrl("/api/v1.0/authenticate/", {
    silent: "true",
    next: window.location.href,
    ...FORWARDABLE_OIDC_PARAMS.reduce((params, key) => {
        if (searchParams.get(key)) {
            return { ...params, [key]: searchParams.get(key)}
        }
        return params;
    }, {})
  }));
};

/**
 * Check if the silent login can be triggered according to the last retry time
 */
export const canAttemptSilentLogin = () => {
  const lastRetryDate = localStorage.getItem(SILENT_LOGIN_RETRY_KEY);
  if (!lastRetryDate) return true;
  return Date.now() > Number(lastRetryDate);
};

/**
 * Set the next retry time in localStorage
 *
 * Retry time is the current time + SILENT_LOGIN_RETRY_INTERVAL
 */
const setNextRetryTime = () => {
  const nextRetryTime = Date.now() + SILENT_LOGIN_RETRY_INTERVAL;
  localStorage.setItem(SILENT_LOGIN_RETRY_KEY, String(nextRetryTime));
};

/**
 * Attempt to perform a silent login
 *
 * If the silent login can be triggered, it will be performed
 * and the next retry time will be set
 */
export const attemptSilentLogin = () => {
  if (!canAttemptSilentLogin()) return;
  setNextRetryTime();
  silentLogin();
};

import { getRequestUrl } from "../api/utils";
import { SILENT_LOGIN_RETRY_INTERVAL, SILENT_LOGIN_RETRY_KEY } from "../config/constants";

/**
 * Replace the current window location with the silent login URL
 *
 * The silent login URL is the same as the login URL, but with the silent
 * parameter set to true and the `next` parameter set to the current window
 * location so the OIDC callback redirects the user back to where they were.
 */
const silentLogin = () => {
  window.location.replace(getRequestUrl("/api/v1.0/authenticate/", {
    silent: "true",
    next: window.location.href,
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

/**
 * Push client helpers shared by the two device-registration flows: Web Push
 * (`features/layouts/components/mailbox-settings/devices-view/web-push.ts`)
 * and the native Capacitor shells (`features/native/push.ts`). Both register
 * through the same `POST /users/me/channels/` upsert; these helpers keep the
 * opt-in semantics and the token-hash contract identical across transports.
 */
import { APP_STORAGE_PREFIX } from "@/features/config/constants";

/** Per-(device, user) opt-in marker. The push registration (browser
 * subscription or native OS token) and localStorage are per-device, *not*
 * per-user, so this flag is what tells "this user enabled push on this device"
 * apart from "a registration merely exists" (e.g. one left behind by a
 * previous user on a shared device). It gates the on-load (re-)registration:
 * only a user who explicitly enabled push here gets their server channel
 * silently (re)created on login — a voluntary logout deletes that channel
 * server-side, and this marker is what makes the same user's notifications
 * resume transparently while never enrolling a different user on the same
 * device. */
const pushOptInKey = (userId: string): string =>
  `${APP_STORAGE_PREFIX}push-opt-in.${userId}`;

export const setPushOptIn = (userId: string | undefined): void => {
  if (!userId) return;
  try {
    localStorage.setItem(pushOptInKey(userId), "1");
  } catch {
    // Private mode / storage disabled: the user just re-enables manually.
  }
};

export const clearPushOptIn = (userId: string | undefined): void => {
  if (!userId) return;
  try {
    localStorage.removeItem(pushOptInKey(userId));
  } catch {
    // Best-effort.
  }
};

export const hasPushOptIn = (userId: string | undefined): boolean => {
  if (!userId) return false;
  try {
    return localStorage.getItem(pushOptInKey(userId)) === "1";
  } catch {
    return false;
  }
};

/** SHA-256 hex of a push token, byte-for-byte identical to the backend's
 * `Channel.lookup_hash` (`sha256("push:" + token).hexdigest()`, see
 * `_token_hash`). The `push:` prefix namespaces the input so the globally-unique
 * hash can't collide with another channel type — the frontend must mirror it
 * exactly. Lets the app match *this* device's registration (Web Push endpoint
 * or native token) to a server-listed device row (which exposes only the hash,
 * never the token). */
export const hashEndpoint = async (endpoint: string): Promise<string> => {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(`push:${endpoint}`),
  );
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
};

/** Mirror of `PUSH_RECEIVED` in `public/sw.js`: the content-free notice the
 * worker posts to open tabs on every push it handles. */
const PUSH_RECEIVED = "push-received";

/**
 * Subscribe to the worker's "a push just arrived" notice, and return a cleanup
 * that removes the listener.
 *
 * Only Web Push routes through a service worker, so this is a no-op on the
 * native shells and wherever service workers are unavailable — callers must
 * treat it as an accelerator on top of whatever they already poll, never as the
 * only way they learn about new mail.
 */
export const listenForPushReceived = (onPush: () => void): (() => void) => {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return () => {};
  }
  const handler = (event: MessageEvent): void => {
    const data = event.data as { type?: unknown } | null | undefined;
    if (data?.type === PUSH_RECEIVED) onPush();
  };
  navigator.serviceWorker.addEventListener("message", handler);
  return () => navigator.serviceWorker.removeEventListener("message", handler);
};

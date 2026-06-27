/**
 * Web Push enable flow for browsers / installed PWAs.
 *
 * Native (Capacitor) builds use the OS push plugins instead — this path is only
 * for the `web` transport. Registers the service worker, requests notification
 * permission, subscribes with the server's VAPID public key, and registers the
 * resulting subscription as a user-scoped `push` channel via the API.
 */
import {
  PlatformEnum,
  PushChannelCreateTypeEnum,
  usersMeChannelsCreate,
  usersMeChannelsList,
} from "@/features/api/gen";
import { getApiOrigin } from "@/features/api/utils";
import i18n from "@/features/i18n/initI18n";
import {
  clearPushOptIn,
  hashEndpoint,
  hasPushOptIn,
  setPushOptIn,
} from "@/features/push/shared";

/**
 * The service worker's push handler enriches notifications by fetching the
 * message over the authenticated session, but `sw.js` is a static file that
 * can't read `import.meta.env`. When the API lives on a different origin than
 * the app (dev: front `:8900` / API `:8901`), a same-origin fetch never reaches
 * the backend and enrichment silently falls back to a generic banner. So we
 * carry the API origin in the registration URL's query string: it becomes part
 * of the stored `scriptURL`, so `self.location.search` reads it back even when
 * the worker cold-starts for a headless push (no page open). Scope stays `/`
 * (query params don't affect scope), so the push subscription survives this
 * script-url change.
 *
 * We also carry the current VAPID public key (`?vapid=`): the worker's
 * `pushsubscriptionchange` handler must re-subscribe with the *current* key, not
 * the one on the (possibly rotated-away) old subscription, or it would recreate a
 * dead subscription. A key rotation changes this URL, so the next re-register
 * pulls the new `?vapid=`.
 *
 * And the UI language (`?lang=`): the worker's generic fallback banner (shown
 * when enrichment fails) must speak the user's language, and it can't load
 * i18next. A language switch reaches the worker through the same on-load
 * re-registration as the other params.
 */
const swUrl = (vapidPublicKey: string): string =>
  `/sw.js?api=${encodeURIComponent(getApiOrigin())}&vapid=${encodeURIComponent(
    vapidPublicKey,
  )}&lang=${encodeURIComponent(i18n.resolvedLanguage ?? i18n.language ?? "en")}`;

export type EnableWebPushResult =
  | "subscribed"
  | "denied" // permission explicitly refused (needs OS/browser settings)
  | "dismissed" // prompt closed without choosing — retrying is fine
  | "unsupported"
  | "registration_failed" // the service worker (/sw.js) failed to register
  | "push_service_error"; // browser↔push-service handshake failed (e.g. Brave)

/** True when this browser can do Web Push at all. */
export const isWebPushSupported = (): boolean =>
  typeof navigator !== "undefined" &&
  "serviceWorker" in navigator &&
  typeof window !== "undefined" &&
  "PushManager" in window &&
  "Notification" in window;

/** Decode a base64url VAPID key into the BufferSource subscribe() expects.
 * Backed by an explicit ArrayBuffer so the type is the non-shared
 * Uint8Array<ArrayBuffer> applicationServerKey requires.
 *
 * Throws if the result isn't a 65-byte uncompressed P-256 point: that's the
 * only shape a VAPID applicationServerKey can be, and a malformed key (e.g. a
 * mis-pinned PUSH_VAPID_PUBLIC_KEY) otherwise fails later inside subscribe()
 * with an opaque error. Failing here surfaces the real cause. */
export const urlBase64ToUint8Array = (
  base64String: string,
): Uint8Array<ArrayBuffer> => {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) {
    output[i] = raw.charCodeAt(i);
  }
  if (output.length !== 65) {
    throw new Error(
      `Invalid VAPID public key: expected 65 bytes, got ${output.length}`,
    );
  }
  return output;
};

/** Coarse OS label from the UA string. The actual machine/host name is not
 * exposed to a browser (privacy), so the OS is the most specific "which device"
 * hint we can attach to the browser name. */
const osName = (ua: string): string | undefined => {
  if (/Windows NT/.test(ua)) return "Windows";
  if (/(iPhone|iPad|iPod)/.test(ua)) return "iOS";
  if (/Macintosh|Mac OS X/.test(ua)) return "macOS";
  if (/Android/.test(ua)) return "Android";
  if (/Linux/.test(ua)) return "Linux";
  return undefined;
};

/** Best-effort human label so the device list can tell browsers apart.
 * Leads with the OS then the browser (e.g. "macOS — Chrome") so a user with the
 * same browser on several machines can distinguish them; falls back to the
 * browser alone when the OS is unrecognised. The transport ("web") is already
 * shown by the platform icon, so it isn't repeated here. */
const deviceName = (): string => {
  const ua = navigator.userAgent;
  let browser = "Browser";
  if (/Edg\//.test(ua)) browser = "Edge";
  else if (/OPR\//.test(ua) || /Opera/.test(ua)) browser = "Opera";
  else if (/Chrome\//.test(ua)) browser = "Chrome";
  else if (/Firefox\//.test(ua)) browser = "Firefox";
  else if (/Safari\//.test(ua)) browser = "Safari";
  const os = osName(ua);
  return os ? `${os} - ${browser}` : browser;
};

/** This browser's current push subscription, or null when the user never
 * enabled push here (unsupported engine / no SW registration / no subscription).
 * Never registers a worker — purely a read. */
export const getCurrentSubscription =
  async (): Promise<PushSubscription | null> => {
    if (!isWebPushSupported()) return null;
    const registration =
      await navigator.serviceWorker.getRegistration("/sw.js");
    if (!registration) return null;
    return registration.pushManager.getSubscription();
  };

/** `token_hash` of this browser's live subscription, or null without one.
 * Matches the server rows' `token_hash`, so the UI can recognise this device
 * in the list (e.g. hide "enable" when it is already enrolled). */
export const currentWebPushTokenHash = async (): Promise<string | null> => {
  try {
    const subscription = await getCurrentSubscription();
    return subscription ? await hashEndpoint(subscription.endpoint) : null;
  } catch {
    return null;
  }
};

type SubscriptionParts = {
  endpoint: string;
  keys: { p256dh: string; auth: string };
};

/** Pull the endpoint + encryption keys the registration API needs, or null when
 * the subscription is somehow incomplete. */
const subscriptionParts = (
  subscription: PushSubscription,
): SubscriptionParts | null => {
  const json = subscription.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) return null;
  return { endpoint: json.endpoint, keys: { p256dh, auth } };
};

/** Upsert a subscription as the user's web `push` channel. The backend keys on
 * the token hash, so re-running is idempotent. */
const registerWebPushSubscription = (
  parts: SubscriptionParts,
): Promise<unknown> =>
  usersMeChannelsCreate({
    type: PushChannelCreateTypeEnum.push,
    platform: PlatformEnum.web,
    token: parts.endpoint,
    keys: parts.keys,
    name: deviceName(),
  });

/** Normalise a base64 / base64url key to unpadded base64url, so a `/config` key
 * with padding or standard `+//` alphabet compares equal to the browser-derived
 * one (else every load would spuriously look "stale"). */
const toBase64Url = (s: string): string =>
  s.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** Unpadded base64url of the subscription's `applicationServerKey`, or null when
 * the engine doesn't expose it (older Safari) — in which case we can't prove a
 * mismatch and must keep the existing subscription. */
const subscriptionServerKey = (
  subscription: PushSubscription,
): string | null => {
  const raw = subscription.options?.applicationServerKey;
  if (!raw) return null;
  const bytes = new Uint8Array(raw);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return toBase64Url(btoa(binary));
};

/** True when the existing subscription was created with a *different* VAPID key
 * than the server now advertises. After a server-side key rotation the push
 * service rejects the old subscription (401/403) forever — the backend can't
 * detect this (it only prunes on 404/410), so the client must tear the dead
 * subscription down and re-create it with the current key. */
export const isStaleForKey = (
  subscription: PushSubscription,
  vapidPublicKey: string,
): boolean => {
  const current = subscriptionServerKey(subscription);
  return current !== null && current !== toBase64Url(vapidPublicKey);
};

/** If the given server device row (identified by its `token_hash`) is *this*
 * browser's subscription, unsubscribe locally first — otherwise the on-load
 * `refreshWebPushSubscription` would immediately recreate the channel we just
 * deleted. No-op for a remote device or when this browser has no subscription.
 * Best-effort. */
export const unsubscribeIfCurrentBrowser = async (
  tokenHash: string | null | undefined,
  userId?: string,
): Promise<void> => {
  if (!tokenHash) return;
  try {
    const subscription = await getCurrentSubscription();
    if (!subscription) return;
    const localHash = await hashEndpoint(subscription.endpoint);
    if (localHash === tokenHash) {
      await subscription.unsubscribe();
      // Explicit opt-out on this device: drop the marker so the on-load refresh
      // doesn't auto-re-subscribe this user next time.
      clearPushOptIn(userId);
    }
  } catch {
    // Best-effort; sign-out proceeds regardless.
  }
};

/**
 * Run the full enable flow. Returns a discriminated outcome for every expected
 * non-success case (so the caller can show an accurate message); throws only on
 * truly unexpected failures.
 *
 * - "dismissed": the user closed the permission prompt without choosing — safe
 *   to retry, nothing is blocked.
 * - "denied": permission explicitly refused — needs OS/browser settings.
 * - "push_service_error": permission granted but `subscribe()` failed to register
 *   with the browser's push service (an `AbortError`). The classic cause is Brave
 *   with "Use Google services for push messaging" off, or no network to the push
 *   service — retryable, but needs that setting flipped.
 */
export const enableWebPush = async (
  vapidPublicKey: string,
  userId?: string,
): Promise<EnableWebPushResult> => {
  if (!isWebPushSupported()) {
    return "unsupported";
  }

  const permission = await Notification.requestPermission();
  if (permission === "denied") {
    return "denied";
  }
  if (permission !== "granted") {
    return "dismissed"; // "default" — prompt closed without a choice
  }

  let registration: ServiceWorkerRegistration;
  try {
    registration = await navigator.serviceWorker.register(swUrl(vapidPublicKey));
    await navigator.serviceWorker.ready;
  } catch {
    // /sw.js missing (404), blocked by CSP, or otherwise unregisterable —
    // distinct from a permission or push-service failure.
    return "registration_failed";
  }

  // Reuse an existing subscription only if it was created with the *current*
  // VAPID key; after a server key rotation the old subscription is dead (the
  // push service returns 401/403), so drop it and subscribe afresh.
  let subscription = await registration.pushManager.getSubscription();
  if (subscription && isStaleForKey(subscription, vapidPublicKey)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  if (!subscription) {
    try {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      });
    } catch (err) {
      // `subscribe()` rejects with AbortError ("Registration failed - push
      // service error") when the browser can't reach/register with its push
      // service. Report it distinctly rather than as a generic failure.
      if (err instanceof DOMException && err.name === "AbortError") {
        // Surface the browser's diagnostic for support cases: this handshake
        // is browser-internal, so without this warn the failure leaves no
        // trace at all in DevTools (no console error, no network request).
        console.warn("Web Push subscribe failed:", err.message);
        return "push_service_error";
      }
      throw err;
    }
  }

  const parts = subscriptionParts(subscription);
  if (!parts) {
    throw new Error("Incomplete push subscription");
  }

  // Push devices register through the generic channels create with type=push;
  // the backend upserts on the token's hash (globally unique), so re-running
  // this is idempotent.
  await registerWebPushSubscription(parts);

  // Remember this user's explicit opt-in on this browser so a later login
  // (after a logout teardown) can re-subscribe them automatically.
  setPushOptIn(userId);

  return "subscribed";
};

/**
 * Re-register the *existing* subscription on app load, to self-heal a rotated
 * endpoint and refresh `last_used_at`.
 *
 * Complements the service worker's `pushsubscriptionchange` handler: that fires
 * only while the browser is running and (on non-Chromium engines) can't attach
 * the CSRF token, so this re-posts through the app's normal, CSRF-correct API
 * client whenever the app opens. It acts only for users who explicitly opted in
 * on this browser (permission granted + a live subscription, *or* the persisted
 * per-user opt-in marker); it never prompts and never registers a worker for a
 * user who never enabled push.
 *
 * `userId` gates the re-registration: a voluntary logout deletes the server
 * channel (see `core.signals`), and this refresh is what recreates it on the
 * returning user's next login — silently, but only if *they* are the one who
 * enabled push here (`hasPushOptIn`). A different user on a shared computer —
 * with no marker — is left untouched, so they never inherit the subscription.
 *
 * When a live subscription exists it *does* re-`register(swUrl(...))`, so a
 * changed `sw.js` body and the `?api=`/`?vapid=` params self-heal on every app
 * load rather than only when the user re-toggles push in settings. This is safe:
 * permission is already granted (no prompt) and the scope stays `/`, so the
 * existing push subscription survives the script-url refresh. It also
 * re-subscribes with the current VAPID key if the key rotated. Best-effort: any
 * failure is swallowed.
 */
export const refreshWebPushSubscription = async (
  vapidPublicKey: string,
  userId?: string,
): Promise<void> => {
  try {
    if (!isWebPushSupported() || !vapidPublicKey) return;
    if (Notification.permission !== "granted") return;

    // Gate on an existing registration so we stay passive for never-opted-in
    // users, then re-register to pull the latest sw.js and the current
    // `?api=`/`?vapid=`.
    const existing = await navigator.serviceWorker.getRegistration("/sw.js");
    if (!existing) return; // user never enabled push in this browser
    const registration = await navigator.serviceWorker.register(
      swUrl(vapidPublicKey),
    );

    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      // No live subscription. Re-subscribe only if THIS user opted in on this
      // browser — otherwise stay passive so a different user on a shared
      // computer isn't silently enrolled.
      if (!hasPushOptIn(userId)) return;
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      });
    } else {
      // A live subscription is NOT proof this user opted in: on a shared
      // computer it may be a previous user's leftover (a voluntary logout only
      // deletes the server channel; an expired session deletes nothing).
      // Entitlement = the opt-in marker, or — for users who enabled push
      // before the marker existed — server-side ownership of this endpoint
      // (their device list contains its hash).
      if (!hasPushOptIn(userId)) {
        const localHash = await hashEndpoint(subscription.endpoint);
        const response = await usersMeChannelsList();
        const owned = (response.data ?? []).some(
          (c) => c.type === "push" && c.token_hash === localHash,
        );
        if (!owned) {
          // A *different* user is now authenticated in this browser: the
          // previous user's device must stop alerting. We can't DELETE their
          // channel (not ours), but unsubscribing is browser-local and needs
          // no permission — the endpoint dies at the push service, so nothing
          // is delivered anymore, and the orphaned server channel self-prunes
          // on its next send (404/410 → stale). The previous user's opt-in
          // marker survives, so when THEY next log in here they get a fresh
          // subscription automatically.
          await subscription.unsubscribe();
          return;
        }
      }
      if (isStaleForKey(subscription, vapidPublicKey)) {
        // Key rotated under us: the existing subscription is dead, so
        // re-subscribe with the current key. Permission is already granted
        // (no prompt), so this stays on the passive path.
        await subscription.unsubscribe();
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
        });
      }
    }

    const parts = subscriptionParts(subscription);
    if (!parts) return;

    await registerWebPushSubscription(parts);
    // (Re)assert the marker: entitlement was established above, and legacy
    // users (opted in before the marker existed) get migrated onto it here.
    setPushOptIn(userId);
  } catch {
    // Best-effort refresh — never disrupt app load.
  }
};

/** Mirror of `PUSH_SUBSCRIPTION_CHANGED` in `public/sw.js`: the message the
 * worker posts after it re-subscribes on `pushsubscriptionchange`. */
const PUSH_SUBSCRIPTION_CHANGED = "push-subscription-changed";

type PushSubscriptionChangedMessage = {
  type: typeof PUSH_SUBSCRIPTION_CHANGED;
  subscription: { endpoint: string; keys: { p256dh: string; auth: string } };
};

const isPushSubscriptionChangedMessage = (
  data: unknown,
): data is PushSubscriptionChangedMessage => {
  if (typeof data !== "object" || data === null) return false;
  const msg = data as Record<string, unknown>;
  if (msg.type !== PUSH_SUBSCRIPTION_CHANGED) return false;
  const sub = msg.subscription as Record<string, unknown> | undefined;
  const keys = sub?.keys as Record<string, unknown> | undefined;
  return (
    typeof sub?.endpoint === "string" &&
    typeof keys?.p256dh === "string" &&
    typeof keys?.auth === "string"
  );
};

/**
 * Register the app-side listener for the worker's `pushsubscriptionchange`
 * hand-off, and register the new endpoint through the app's API client.
 *
 * The worker re-`subscribe()`s on its own, but under `CSRF_USE_SESSIONS` it
 * can't sign the registration POST — the CSRF token is no longer a cookie, it
 * lives in this page's memory (delivered via `/users/me/`). So the worker posts
 * the fresh subscription here and the app registers it with the correct CSRF
 * header. Returns a cleanup that removes the listener; no-ops where service
 * workers are unavailable. `refreshWebPushSubscription` remains the fallback
 * when no page was open at rotation time.
 */
export const listenForPushSubscriptionChange = (): (() => void) => {
  if (!isWebPushSupported()) return () => {};
  const handler = (event: MessageEvent): void => {
    if (!isPushSubscriptionChangedMessage(event.data)) return;
    void registerWebPushSubscription(event.data.subscription).catch(() => {
      // Best-effort; refreshWebPushSubscription reconciles on the next load.
    });
  };
  navigator.serviceWorker.addEventListener("message", handler);
  return () => navigator.serviceWorker.removeEventListener("message", handler);
};

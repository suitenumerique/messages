/* Web Push service worker.
 *
 * Registered by the app (see features/.../devices-view/web-push.ts) when the
 * user enables notifications in a browser.
 *
 * The push payload is deliberately thin and content-free — only routing ids +
 * the unread count. To show a Gmail-style banner (sender + subject) WITHOUT
 * ever putting content on the push transport, we fetch the message over the
 * user's own authenticated session here and rewrite the notification
 * ("fetch-to-enrich"). If that fetch fails (offline, cross-origin API, signed
 * out) we fall back to a generic content-free banner.
 *
 * `userVisibleOnly: true` subscriptions must surface a notification for every
 * push — with one exception the browsers grant precisely because the user
 * already sees the news: a focused app window (see handlePush).
 */
/* global self, clients, fetch */

// Take over as soon as a new version installs instead of sitting in the
// "waiting" state until every controlled tab closes. This SW does no offline
// fetch caching, so there is no stale-cache hazard in activating early — it just
// means a pushed sw.js change (e.g. a new `?api=` origin or enrichment fix)
// starts serving on the next load rather than days later.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
  event.waitUntil(self.clients.claim()),
);

// API origin, carried in the registration URL's query by the app (web-push.ts)
// because this static file can't read the build env. It lets enrichment reach
// the backend even when the API is on a different origin than the app (dev:
// front :8900 / API :8901). Empty string ⇒ same-origin, matching the previous
// behaviour and covering an older registration that predates the `?api=` param.
const API_ORIGIN =
  new URLSearchParams(self.location.search).get("api") || "";
const MESSAGE_URL = (id) =>
  `${API_ORIGIN}/api/v1.0/messages/${encodeURIComponent(id)}/`;

// Current server VAPID public key, carried in the registration URL's query by
// the app (web-push.ts) for the same reason as `?api=` — a static file can't
// read the build env. Used by `pushsubscriptionchange` to re-subscribe with the
// *current* key rather than the one on the (possibly rotated-away) old
// subscription. Empty for a registration that predates the `?vapid=` param.
const VAPID_PUBLIC_KEY =
  new URLSearchParams(self.location.search).get("vapid") || "";

// UI language, carried in the registration URL's query like `?api=` — a static
// file can't reach i18next, and only the generic-banner fallback below needs
// translating (an enriched banner shows the real sender/subject). The pair
// mirrors the "New message" key in public/locales (FR + EN, the two supported
// languages); the on-load re-registration keeps the param in step with the
// user's language. Empty (a registration predating `?lang=`) ⇒ English.
const LANG = new URLSearchParams(self.location.search).get("lang") || "en";
const GENERIC_TITLE = /^fr\b/i.test(LANG) ? "Nouveau message" : "New message";

// Decode a base64url VAPID key into the Uint8Array subscribe() expects. Mirrors
// urlBase64ToUint8Array in web-push.ts.
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) {
    output[i] = raw.charCodeAt(i);
  }
  return output;
}

const senderLabel = (message) => {
  const s = message && message.sender;
  return (s && (s.name || s.email)) || GENERIC_TITLE;
};

async function buildNotification(payload) {
  // Default: content-free banner (what the thin payload alone can show).
  let title = GENERIC_TITLE;
  const tag = payload.thread_id ? "thread-" + payload.thread_id : undefined;
  const options = {
    body: "",
    // App icon shown on the banner. Served from the app origin (this SW's
    // scope), not API_ORIGIN, so it resolves same-origin. Without it the
    // platform falls back to the favicon/PWA icon, which is inconsistent
    // across browsers.
    icon: "/assets/icons/icon-192.webp",
    // Monochrome silhouette Android paints into the status bar when the full
    // icon doesn't fit. Only the alpha channel is used (Android tints the
    // shape), so this must stay a transparent monochrome PNG — not a color
    // .webp. Ignored on desktop/Firefox/Safari.
    badge: "/assets/icons/icon-mono-72.png",
    // Coalesce a burst in one thread into a single notification...
    tag,
    // ...but still re-alert (sound/vibrate) for each new message in that thread,
    // rather than silently swapping the banner. renotify requires a tag.
    renotify: Boolean(tag),
    data: payload,
  };

  // Fetch-to-enrich: pull sender + subject over the authenticated session.
  if (payload.message_id) {
    try {
      const resp = await fetch(MESSAGE_URL(payload.message_id), {
        credentials: "include",
        headers: { Accept: "application/json" },
        // A hung network (captive portal) must not stall showNotification past
        // the OS's patience — Chrome would show its own generic "site updated
        // in the background" instead. The catch keeps the generic banner.
        signal: AbortSignal.timeout ? AbortSignal.timeout(5000) : undefined,
      });
      if (resp.ok) {
        const message = await resp.json();
        title = senderLabel(message);
        options.body = message.subject || "";
      }
    } catch {
      // Offline / cross-origin / signed out — keep the generic banner.
    }
  }

  // Drive the installed-PWA app badge from the unread count the payload carries.
  // Guarded: Firefox/Safari (and non-installed contexts) lack the Badging API.
  if ("setAppBadge" in self.navigator && typeof payload.unread_count === "number") {
    self.navigator.setAppBadge(payload.unread_count).catch(() => {});
  }

  return self.registration.showNotification(title, options);
}

// Message type the worker posts to the app on every push it handles. An open
// tab sitting in the background has no other way to learn that mail arrived
// before its next mailbox poll; it uses this to raise the favicon badge at once
// (see features/providers/use-unread-badge.ts). Content-free by design — the
// app re-reads its own state, the message is only a "go look now" nudge.
const PUSH_RECEIVED = "push-received";

async function handlePush(payload) {
  // Best-effort: a failed lookup must not swallow the banner, so fall through
  // with an empty list (no client to nudge, none focused ⇒ notify).
  let windowClients = [];
  try {
    windowClients = await clients.matchAll({
      type: "window",
      includeUncontrolled: true,
    });
  } catch {
    // Keep going: the banner matters more than the nudge.
  }

  for (const client of windowClients) {
    client.postMessage({ type: PUSH_RECEIVED });
  }

  // The user is looking at the app right now: the mail lands in the list on its
  // own (30s mailbox poll, see features/providers/mailbox.tsx), so a banner would
  // announce what is already on its way onto their screen — and an app badge
  // would contradict the clear-on-foreground rule the rest of the app follows.
  // This is the one case `userVisibleOnly` tolerates a silent push: browsers only
  // substitute their own "site updated in the background" notice when no window
  // of the origin is visible, and a focused window is visible. Mirrors the native
  // side, where iOS foreground presentation is limited to the badge
  // (capacitor.config.ts) and Android in foreground never auto-displays.
  if (windowClients.some((client) => client.focused)) {
    return;
  }

  return buildNotification(payload);
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  event.waitUntil(handlePush(payload));
});

// Deep-link target from the routing ids the payload carries. mailbox_id is the
// recipient's mailbox the thread is read in (added to the thin payload precisely
// so we can route here); without it we can only open the app root. `has_active`
// puts the thread list behind the tap on the inbox filter.
//
// The message goes in the *hash*, not a query param: the thread view scrolls to
// (and highlights) `#thread-message-{id}` on mount, whereas an unknown query key
// is dropped by THREADS_LIST_NUMERIC_FILTERS' allow-list — so `?message_id=`
// landed on the thread without ever reaching the message.
// Mirrored by pushTargetUrl in features/native/push.ts — keep the two in sync.
const targetUrl = (payload) => {
  if (!payload || !payload.mailbox_id || !payload.thread_id) {
    return "/";
  }
  const url = new URL(
    `/mailbox/${payload.mailbox_id}/thread/${payload.thread_id}`,
    self.location.origin,
  );
  url.searchParams.set("has_active", "1");
  if (payload.message_id) {
    url.hash = `thread-message-${payload.message_id}`;
  }
  return url.toString();
};

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = targetUrl(event.notification.data);

  event.waitUntil(
    (async () => {
      // Arriving in the app from a notification: drop the badge. The push
      // handler is the only writer that raises it, so clearing on tap keeps it
      // from lingering once the user has acknowledged the alert. The app also
      // clears it on foreground (see features/auth) for icon-launch opens.
      // Guarded + best-effort — Badging API absent on Firefox/Safari.
      if ("clearAppBadge" in self.navigator) {
        self.navigator.clearAppBadge().catch(() => {});
      }

      const windowClients = await clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });

      // Reuse an existing app tab: prefer the focused one, else the first
      // window matchAll returns (all are same-origin app windows). The previous
      // loop returned on the first tab regardless of focus.
      const client = windowClients.find((c) => c.focused) || windowClients[0];

      // Open a fresh window at the deep-link. Also the fallback when navigate()
      // can't run, so it lives in one place.
      const openFresh = () =>
        clients.openWindow ? clients.openWindow(url) : undefined;

      if (!client || !("focus" in client)) {
        return openFresh();
      }

      // Bring the tab forward first, so a tap always surfaces the app even when
      // the follow-up navigate() can't run (root url, or a rejection below).
      const focused = (await client.focus()) || client;

      if (url === "/" || !("navigate" in focused)) {
        return focused;
      }

      // navigate() only works on a client *controlled* by this SW; on an
      // uncontrolled tab (e.g. one hard-reloaded past the SW, which
      // includeUncontrolled still surfaces) it rejects with a TypeError. Without
      // this catch the tap would silently drop the deep-link — fall back to
      // opening a fresh, controlled window instead.
      try {
        return await focused.navigate(url);
      } catch {
        return openFresh();
      }
    })(),
  );
});

// Message type the worker posts to the app when it re-subscribes; the app
// listens for it and registers the new endpoint through its CSRF-correct client.
const PUSH_SUBSCRIPTION_CHANGED = "push-subscription-changed";

// The browser can rotate or expire our push subscription at any time (clearing
// site data, periodic rotation, key changes). Without this the user silently
// stops receiving pushes until they revisit settings. Re-subscribe with the
// same VAPID key (carried on the old subscription); registering the new endpoint
// on the backend is an authenticated, CSRF-protected POST that the worker can no
// longer sign itself — under CSRF_USE_SESSIONS the token is not a readable
// cookie but lives in the app page's memory (delivered via /users/me/). So we
// hand the new subscription to any open client, which re-registers it through
// the app's API client; if no client is open, the subscription persists locally
// and refreshWebPushSubscription re-registers it on the next app load.
self.addEventListener("pushsubscriptionchange", (event) => {
  // Prefer the current server key (injected in this SW's script URL) over the
  // one on the old subscription: on a key rotation the old key is exactly what
  // the push service now rejects, so re-subscribing with it would recreate a
  // dead subscription. Fall back to the old key for registrations predating the
  // `?vapid=` param.
  const applicationServerKey = VAPID_PUBLIC_KEY
    ? urlBase64ToUint8Array(VAPID_PUBLIC_KEY)
    : event.oldSubscription &&
      event.oldSubscription.options &&
      event.oldSubscription.options.applicationServerKey;

  event.waitUntil(
    (async () => {
      try {
        const subscription = await self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey,
        });
        const json = subscription.toJSON();
        const p256dh = json.keys && json.keys.p256dh;
        const auth = json.keys && json.keys.auth;
        if (!json.endpoint || !p256dh || !auth) {
          return;
        }
        const windowClients = await clients.matchAll({
          type: "window",
          includeUncontrolled: true,
        });
        for (const client of windowClients) {
          client.postMessage({
            type: PUSH_SUBSCRIPTION_CHANGED,
            subscription: { endpoint: json.endpoint, keys: { p256dh, auth } },
          });
        }
      } catch {
        // Best-effort self-heal; the app's on-load refresh is the fallback.
      }
    })(),
  );
});

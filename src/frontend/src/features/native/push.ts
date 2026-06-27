/**
 * Native (Capacitor) push enable flow for the iOS / Android shells.
 *
 * Mirror of the Web Push client (`devices-view/web-push.ts`) on the native
 * transports: `@capacitor/push-notifications` yields the APNs (iOS) / FCM
 * (Android) device token and the app registers it as a user-scoped `push`
 * channel through the same `POST /users/me/channels/` upsert. Unlike Web Push
 * no VAPID key is involved — the OS plugins carry their own credentials (APNs
 * entitlement, bundled google-services.json). Token rotation is caught by the
 * idempotent on-launch re-registration (`refreshNativePushRegistration`), the
 * pattern docs/push-notifications.md §4 recommends over a long-lived
 * `registration` listener.
 */
import { App } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";
import { Device } from "@capacitor/device";
import {
  ActionPerformed,
  PushNotifications,
  RegistrationError,
  Token,
} from "@capacitor/push-notifications";

import {
  PlatformEnum,
  PushChannelCreateTypeEnum,
  usersMeChannelsCreate,
} from "@/features/api/gen";
import { APP_STORAGE_PREFIX } from "@/features/config/constants";
import i18n from "@/features/i18n/initI18n";
import {
  clearPushOptIn,
  hashEndpoint,
  hasPushOptIn,
  setPushOptIn,
} from "@/features/push/shared";

import { isNativePlatform } from "./platform";

/** Last token registered from this device. A push token is a device-held
 * routing id, not a credential (see docs/push-notifications.md §9); it is kept
 * so device sign-out can match this device's server row (`token_hash`) without
 * re-driving the OS registration. */
const NATIVE_TOKEN_KEY = `${APP_STORAGE_PREFIX}push-native-token`;

/** APNs/FCM answer registration over the network; leave room for a slow radio
 * before reporting the enable attempt as failed. */
const REGISTRATION_TIMEOUT_MS = 15_000;

/** Android notification channel our pushes render on. Mirror of
 * FCM_ANDROID_CHANNEL_ID (core/services/push/fcm.py) and of the manifest's
 * default-channel meta-data — contract-tested on both sides. */
export const ANDROID_NOTIFICATION_CHANNEL_ID = "new_messages";

/** Create the Android channel (idempotent) before the OS registration.
 *
 * Without it, Android 8+ renders FCM messages on the SDK's anonymous
 * "Miscellaneous" fallback at DEFAULT importance — no heads-up banner, and an
 * unnamed entry in the system notification settings — whatever the message
 * priority says. Importance is only honored at creation (re-calls can rename,
 * never upgrade), hence HIGH from the very first call. iOS has no channels.
 * Best-effort: delivery still works through the fallback channel if this
 * fails. */
const ensureAndroidNotificationChannel = async (): Promise<void> => {
  if (Capacitor.getPlatform() !== "android") return;
  try {
    await PushNotifications.createChannel({
      id: ANDROID_NOTIFICATION_CHANNEL_ID,
      name: i18n.t("New messages"),
      description: i18n.t("Alerts for new messages in your mailboxes"),
      importance: 4, // IMPORTANCE_HIGH — heads-up banner
    });
  } catch {
    // Never block the registration flow on a cosmetic failure.
  }
};

export type EnableNativePushResult =
  | "registered"
  | "denied" // permission refused — needs the OS app settings
  | "unsupported" // not running inside a native shell
  | "registration_failed"; // OS/gateway registration failed — retryable

/** `platform` is a transport, not an OS: the iOS shell registers the APNs
 * token, the Android shell the FCM token (docs/push-notifications.md §2). */
const nativePlatform = (): PlatformEnum =>
  Capacitor.getPlatform() === "ios" ? PlatformEnum.apns : PlatformEnum.fcm;

/** Human device label for the settings list. Prefer the user-assigned device
 * name where the OS exposes it (recent iOS returns a generic "iPhone" without
 * a special entitlement), falling back to the hardware model. */
const deviceLabel = async (): Promise<string | undefined> => {
  try {
    const info = await Device.getInfo();
    return info.name || info.model;
  } catch {
    return undefined;
  }
};

const appVersion = async (): Promise<string | undefined> => {
  try {
    return (await App.getInfo()).version;
  } catch {
    return undefined;
  }
};

/** Drive the OS registration and resolve with the device token. Listeners are
 * attached before `register()` so an immediately-emitted `registration` event
 * can't be missed; both are removed once settled. */
const obtainToken = async (): Promise<string> => {
  let settle: (result: { token?: string; error?: string }) => void = () => {};
  const outcome = new Promise<{ token?: string; error?: string }>((resolve) => {
    settle = resolve;
  });
  const handles = await Promise.all([
    PushNotifications.addListener("registration", (token: Token) =>
      settle({ token: token.value }),
    ),
    PushNotifications.addListener(
      "registrationError",
      (event: RegistrationError) => settle({ error: event.error }),
    ),
  ]);
  const timeout = setTimeout(
    () => settle({ error: "timed out" }),
    REGISTRATION_TIMEOUT_MS,
  );
  try {
    await PushNotifications.register();
    const result = await outcome;
    if (!result.token) {
      throw new Error(`Push registration failed: ${result.error}`);
    }
    return result.token;
  } finally {
    clearTimeout(timeout);
    handles.forEach((handle) => void handle.remove());
  }
};

/** Upsert this device's token as the user's native `push` channel. The backend
 * keys on the token hash, so re-running is idempotent. */
const registerNativeDevice = async (token: string): Promise<void> => {
  await usersMeChannelsCreate({
    type: PushChannelCreateTypeEnum.push,
    platform: nativePlatform(),
    token,
    name: await deviceLabel(),
    app_version: await appVersion(),
  });
  try {
    localStorage.setItem(NATIVE_TOKEN_KEY, token);
  } catch {
    // Storage unavailable: only device sign-out matching degrades.
  }
};

/**
 * Run the full enable flow: OS permission prompt (only when still undecided),
 * OS registration, channel upsert. Returns a discriminated outcome for every
 * expected non-success case so the caller can show an accurate message.
 */
export const enableNativePush = async (
  userId?: string,
): Promise<EnableNativePushResult> => {
  if (!isNativePlatform()) {
    return "unsupported";
  }

  let permission = await PushNotifications.checkPermissions();
  if (
    permission.receive === "prompt" ||
    permission.receive === "prompt-with-rationale"
  ) {
    permission = await PushNotifications.requestPermissions();
  }
  if (permission.receive !== "granted") {
    // iOS only lets the app ask once; afterwards only the OS settings can
    // flip it, so "denied" tells the UI to point there.
    return "denied";
  }

  await ensureAndroidNotificationChannel();

  try {
    await registerNativeDevice(await obtainToken());
  } catch {
    return "registration_failed";
  }

  // Remember this user's explicit opt-in on this device so a later login
  // (after a logout teardown) can re-register them automatically.
  setPushOptIn(userId);
  return "registered";
};

/**
 * Idempotent on-launch re-registration (docs/push-notifications.md §4): catches
 * a silently rotated token and refreshes `last_used_at`. Passive — only for a
 * user who explicitly enabled push on this device (opt-in marker) and only when
 * the OS permission is already granted; never prompts. This is also what
 * recreates the server channel after a voluntary logout (deleted server-side)
 * on the same user's next login. Unlike the web flow there is no shared-device
 * teardown here: without the marker we simply stay passive, and a different
 * user who enables push triggers the server-side token reclaim instead.
 */
export const refreshNativePushRegistration = async (
  userId?: string,
): Promise<void> => {
  try {
    if (!isNativePlatform() || !hasPushOptIn(userId)) return;
    const permission = await PushNotifications.checkPermissions();
    if (permission.receive !== "granted") return;
    await ensureAndroidNotificationChannel();
    await registerNativeDevice(await obtainToken());
  } catch {
    // Best-effort refresh — never disrupt app load.
  }
};

/** `token_hash` of this device's last registered token, or null before any
 * registration (or after a sign-out, which clears it). Matches the server
 * rows' `token_hash`, so the UI can recognise this device in the list (e.g.
 * hide "enable" when it is already enrolled). */
export const currentNativeTokenHash = async (): Promise<string | null> => {
  if (!isNativePlatform()) return null;
  try {
    const token = localStorage.getItem(NATIVE_TOKEN_KEY);
    return token ? await hashEndpoint(token) : null;
  } catch {
    return null;
  }
};

/** Native counterpart of `unsubscribeIfCurrentBrowser`: when the signed-out
 * device row is *this* device (`token_hash` match), stop OS delivery
 * (`unregister` drops the FCM token / APNs registration) and clear the opt-in
 * marker so the on-launch refresh doesn't silently re-register it. No-op for a
 * remote device. Best-effort — the caller DELETEs the server row regardless. */
export const unregisterIfCurrentDevice = async (
  tokenHash: string | null | undefined,
  userId?: string,
): Promise<void> => {
  if (!tokenHash || !isNativePlatform()) return;
  try {
    const token = localStorage.getItem(NATIVE_TOKEN_KEY);
    if (!token || (await hashEndpoint(token)) !== tokenHash) return;
    clearPushOptIn(userId);
    localStorage.removeItem(NATIVE_TOKEN_KEY);
    await PushNotifications.unregister();
  } catch {
    // Best-effort; sign-out proceeds regardless.
  }
};

/** Deep-link target mirroring the Web Push service worker's `targetUrl`
 * (public/sw.js): thread view + `has_active` + optional message anchor. The
 * message goes in the hash because that is what the thread view scrolls to and
 * highlights ("#thread-message-{id}"); a query param would be dropped by the
 * threads-list allow-list. The payload carries content-free routing ids only
 * ({type, thread_id, message_id, mailbox_id, unread_count}); FCM stringifies
 * every value, so ids are always strings here. */
export const pushTargetUrl = (data: unknown): string => {
  const payload = (data ?? {}) as Record<string, unknown>;
  const mailboxId = payload.mailbox_id;
  const threadId = payload.thread_id;
  if (
    typeof mailboxId !== "string" ||
    !mailboxId ||
    typeof threadId !== "string" ||
    !threadId
  ) {
    return "/";
  }
  const messageId = payload.message_id;
  const hash =
    typeof messageId === "string" && messageId
      ? `#thread-message-${messageId}`
      : "";
  return `/mailbox/${mailboxId}/thread/${threadId}?has_active=1${hash}`;
};

/**
 * Route notification taps to the thread they point at. Registered once at
 * bootstrap — as early as possible so the tap that cold-started the app (the
 * plugin replays it to a freshly added listener) is not missed.
 */
export const listenForNativePushTaps = (
  navigate: (url: string) => void,
): void => {
  if (!isNativePlatform()) return;
  void PushNotifications.addListener(
    "pushNotificationActionPerformed",
    (action: ActionPerformed) =>
      navigate(pushTargetUrl(action.notification.data)),
  );
};

/** Dismiss delivered notifications once the user is actually looking at the
 * app; the foreground counterpart of the web `clearAppBadge` in features/auth
 * (the iOS badge itself is reset natively — see AppDelegate). */
export const clearDeliveredNativeNotifications = (): void => {
  if (!isNativePlatform()) return;
  PushNotifications.removeAllDeliveredNotifications().catch(() => {});
};

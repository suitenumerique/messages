/**
 * The native push client is the iOS/Android counterpart of the Web Push flow:
 * these tests pin the registration contract (platform = transport, opt-in
 * marker gating, token-hash sign-out matching) and the tap deep-link mirroring
 * of the service worker's targetUrl.
 */
import { hashEndpoint, hasPushOptIn, setPushOptIn } from "@/features/push/shared";

// The app-local PushPreviewKeys plugin, handed out by registerPlugin below.
const previewKeysPlugin = vi.hoisted(() => ({
  getKeys: vi.fn(),
  clear: vi.fn(),
}));

// Partial mock: the api client pulled in through @/features/api/gen reaches
// auth-session.ts, which needs the real registerPlugin for its own plugin.
vi.mock("@capacitor/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@capacitor/core")>();
  return {
    ...actual,
    Capacitor: { getPlatform: vi.fn(), isNativePlatform: vi.fn() },
    registerPlugin: (...args: Parameters<typeof actual.registerPlugin>) =>
      args[0] === "PushPreviewKeys"
        ? previewKeysPlugin
        : actual.registerPlugin(...args),
  };
});
vi.mock("@capacitor/device", () => ({
  Device: { getInfo: vi.fn() },
}));
vi.mock("@capacitor/app", () => ({
  App: { getInfo: vi.fn() },
}));
vi.mock("@capacitor/push-notifications", () => ({
  PushNotifications: {
    addListener: vi.fn(),
    register: vi.fn(),
    unregister: vi.fn(),
    checkPermissions: vi.fn(),
    requestPermissions: vi.fn(),
    removeAllDeliveredNotifications: vi.fn(),
    createChannel: vi.fn(),
  },
}));
// The Android channel name/description go through i18n at creation time; the
// real instance would drag the http backend into the test environment.
vi.mock("@/features/i18n/initI18n", () => ({
  default: { t: (key: string) => key },
}));
vi.mock("./platform", () => ({
  isNativePlatform: vi.fn(),
}));
// Repo pattern for the generated client: automock the resource submodule (the
// index re-exports it, so push.ts sees the mock through "@/features/api/gen").
vi.mock("@/features/api/gen/channels/channels");

import { App } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";
import { Device } from "@capacitor/device";
import { PushNotifications } from "@capacitor/push-notifications";

import { usersMeChannelsCreate } from "@/features/api/gen";

import {
  ANDROID_NOTIFICATION_CHANNEL_ID,
  clearPushPreviewKeys,
  currentNativeTokenHash,
  enableNativePush,
  pushTargetUrl,
  refreshNativePushRegistration,
  unregisterIfCurrentDevice,
} from "./push";

import { isNativePlatform } from "./platform";

const USER_ID = "11111111-2222-3333-4444-555555555555";
const OTHER_USER_ID = "66666666-7777-8888-9999-000000000000";
const TOKEN = "apns-device-token-1";
const PREVIEW_KEYS = { p256dh: "BPublicPoint", auth: "AuthSecret" };

const push = vi.mocked(PushNotifications);
const isNative = vi.mocked(isNativePlatform);
const getPlatform = vi.mocked(Capacitor.getPlatform);
const createChannel = vi.mocked(usersMeChannelsCreate);

/** Event listeners registered by the module, replayable per test. */
let listeners: Record<string, Array<(event: unknown) => void>>;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  listeners = {};

  isNative.mockReturnValue(true);
  getPlatform.mockReturnValue("ios");
  // addListener is overloaded per event name; a single generic implementation
  // can't satisfy the union, hence the cast.
  push.addListener.mockImplementation(((
    event: string,
    cb: (event: unknown) => void,
  ) => {
    (listeners[event] ??= []).push(cb);
    return Promise.resolve({ remove: vi.fn() });
  }) as unknown as typeof PushNotifications.addListener);
  // The OS answers the register() call through the "registration" event.
  push.register.mockImplementation(async () => {
    listeners["registration"]?.forEach((cb) => cb({ value: TOKEN }));
  });
  push.checkPermissions.mockResolvedValue({ receive: "prompt" });
  push.requestPermissions.mockResolvedValue({ receive: "granted" });
  vi.mocked(Device.getInfo).mockResolvedValue({
    name: "Mon iPhone",
    model: "iPhone15,3",
  } as Awaited<ReturnType<typeof Device.getInfo>>);
  vi.mocked(App.getInfo).mockResolvedValue({
    version: "1.2.0",
  } as Awaited<ReturnType<typeof App.getInfo>>);
  createChannel.mockResolvedValue({} as Awaited<ReturnType<typeof usersMeChannelsCreate>>);
  previewKeysPlugin.getKeys.mockResolvedValue(PREVIEW_KEYS);
  previewKeysPlugin.clear.mockResolvedValue(undefined);
});

describe("enableNativePush", () => {
  it("registers the APNs token as a push channel and stores the opt-in", async () => {
    await expect(enableNativePush(USER_ID)).resolves.toBe("registered");

    expect(createChannel).toHaveBeenCalledWith({
      type: "push",
      platform: "apns",
      token: TOKEN,
      keys: PREVIEW_KEYS,
      name: "Mon iPhone",
      app_version: "1.2.0",
    });
    expect(hasPushOptIn(USER_ID)).toBe(true);
  });

  it("registers the preview keys of the signed-in user", async () => {
    // The keyring is bound to one account: another account starts afresh.
    await enableNativePush(USER_ID);
    expect(previewKeysPlugin.getKeys).toHaveBeenCalledWith({ userId: USER_ID });
  });

  it("still registers, without preview keys, on a shell lacking the plugin", async () => {
    // An OTA-updated bundle on an older binary: no plugin, generic banners.
    previewKeysPlugin.getKeys.mockRejectedValue(new Error("not implemented"));
    await expect(enableNativePush(USER_ID)).resolves.toBe("registered");
    expect(createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ token: TOKEN, keys: undefined }),
    );
  });

  it("asks for no preview keys without a user to bind them to", async () => {
    await enableNativePush();
    expect(previewKeysPlugin.getKeys).not.toHaveBeenCalled();
  });

  it("maps the Android shell to the fcm transport", async () => {
    getPlatform.mockReturnValue("android");
    await enableNativePush(USER_ID);
    expect(createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "fcm" }),
    );
  });

  it("creates the high-importance Android channel before registering", async () => {
    // Contract with the backend (FCM_ANDROID_CHANNEL_ID, asserted against the
    // same literal in test_push.py) and the manifest meta-data.
    expect(ANDROID_NOTIFICATION_CHANNEL_ID).toBe("new_messages");

    getPlatform.mockReturnValue("android");
    await enableNativePush(USER_ID);
    expect(push.createChannel).toHaveBeenCalledWith(
      expect.objectContaining({
        id: ANDROID_NOTIFICATION_CHANNEL_ID,
        importance: 4,
      }),
    );
  });

  it("creates no channel on iOS (no channel concept there)", async () => {
    await enableNativePush(USER_ID);
    expect(push.createChannel).not.toHaveBeenCalled();
  });

  it("returns denied without registering when the permission is refused", async () => {
    push.requestPermissions.mockResolvedValue({ receive: "denied" });
    await expect(enableNativePush(USER_ID)).resolves.toBe("denied");
    expect(push.register).not.toHaveBeenCalled();
    expect(createChannel).not.toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(false);
  });

  it("reports a registration failure without opting the user in", async () => {
    push.register.mockImplementation(async () => {
      listeners["registrationError"]?.forEach((cb) => cb({ error: "boom" }));
    });
    await expect(enableNativePush(USER_ID)).resolves.toBe(
      "registration_failed",
    );
    expect(hasPushOptIn(USER_ID)).toBe(false);
  });

  it("is unsupported outside the native shell", async () => {
    isNative.mockReturnValue(false);
    await expect(enableNativePush(USER_ID)).resolves.toBe("unsupported");
  });
});

describe("refreshNativePushRegistration", () => {
  it("stays passive without the user's opt-in marker", async () => {
    push.checkPermissions.mockResolvedValue({ receive: "granted" });
    await refreshNativePushRegistration(USER_ID);
    expect(push.register).not.toHaveBeenCalled();
    expect(createChannel).not.toHaveBeenCalled();
  });

  it("never prompts: granted permission is required", async () => {
    setPushOptIn(USER_ID);
    push.checkPermissions.mockResolvedValue({ receive: "prompt" });
    await refreshNativePushRegistration(USER_ID);
    expect(push.requestPermissions).not.toHaveBeenCalled();
    expect(push.register).not.toHaveBeenCalled();
  });

  it("re-registers idempotently for an opted-in user", async () => {
    setPushOptIn(USER_ID);
    push.checkPermissions.mockResolvedValue({ receive: "granted" });
    await refreshNativePushRegistration(USER_ID);
    expect(createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ token: TOKEN }),
    );
  });

  it("re-sends the (possibly rotated) preview keys on every launch", async () => {
    setPushOptIn(USER_ID);
    push.checkPermissions.mockResolvedValue({ receive: "granted" });
    await refreshNativePushRegistration(USER_ID);
    expect(previewKeysPlugin.getKeys).toHaveBeenCalledWith({ userId: USER_ID });
    expect(createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ keys: PREVIEW_KEYS }),
    );
  });

  it("re-ensures the Android channel on refresh", async () => {
    // The channel must exist before the first killed-app push; re-creating is
    // idempotent and follows a language change with a localized rename.
    getPlatform.mockReturnValue("android");
    setPushOptIn(USER_ID);
    push.checkPermissions.mockResolvedValue({ receive: "granted" });
    await refreshNativePushRegistration(USER_ID);
    expect(push.createChannel).toHaveBeenCalledWith(
      expect.objectContaining({ id: ANDROID_NOTIFICATION_CHANNEL_ID }),
    );
  });

  describe("when another account signs in on the device", () => {
    // A's session expired: its server channel and preview keys survived, and
    // would keep showing A's senders and subjects to B.
    beforeEach(async () => {
      await enableNativePush(USER_ID);
      vi.clearAllMocks();
      push.checkPermissions.mockResolvedValue({ receive: "granted" });
    });

    it("tears down the previous account's registration", async () => {
      await refreshNativePushRegistration(OTHER_USER_ID);
      expect(previewKeysPlugin.clear).toHaveBeenCalled();
      expect(push.unregister).toHaveBeenCalled();
      expect(await currentNativeTokenHash()).toBeNull();
      expect(createChannel).not.toHaveBeenCalled();
    });

    it("keeps the previous account's opt-in for its own return", async () => {
      await refreshNativePushRegistration(OTHER_USER_ID);
      expect(hasPushOptIn(USER_ID)).toBe(true);
    });

    it("takes the device over for an account that opted in too", async () => {
      // No unregister: Android deletes the FCM token asynchronously, so
      // register() could be handed the dying token. Rebinding the keyring
      // drops the previous account's keys and the server reclaim its channel.
      setPushOptIn(OTHER_USER_ID);
      await refreshNativePushRegistration(OTHER_USER_ID);
      expect(push.unregister).not.toHaveBeenCalled();
      expect(previewKeysPlugin.getKeys).toHaveBeenCalledWith({
        userId: OTHER_USER_ID,
      });
      expect(createChannel).toHaveBeenCalledWith(
        expect.objectContaining({ token: TOKEN }),
      );
    });

    it("rebinds the keyring before the token round-trip", async () => {
      // A failed registration must not leave the previous account's keys.
      setPushOptIn(OTHER_USER_ID);
      push.register.mockRejectedValue(new Error("offline"));
      await refreshNativePushRegistration(OTHER_USER_ID);
      expect(previewKeysPlugin.getKeys).toHaveBeenCalledWith({
        userId: OTHER_USER_ID,
      });
      expect(createChannel).not.toHaveBeenCalled();
    });

    it("tears down for an account that opted in but revoked the permission", async () => {
      // Nothing re-registers, so nothing would rebind the keyring either.
      setPushOptIn(OTHER_USER_ID);
      push.checkPermissions.mockResolvedValue({ receive: "denied" });
      await refreshNativePushRegistration(OTHER_USER_ID);
      expect(previewKeysPlugin.clear).toHaveBeenCalled();
      expect(push.unregister).toHaveBeenCalled();
      expect(hasPushOptIn(OTHER_USER_ID)).toBe(true);
    });

    it("leaves the device alone for the same account", async () => {
      await refreshNativePushRegistration(USER_ID);
      expect(previewKeysPlugin.clear).not.toHaveBeenCalled();
      expect(push.unregister).not.toHaveBeenCalled();
    });
  });
});

describe("unregisterIfCurrentDevice", () => {
  it("unregisters and clears the opt-in when the row is this device", async () => {
    await enableNativePush(USER_ID);
    await unregisterIfCurrentDevice(await hashEndpoint(TOKEN), USER_ID);
    expect(push.unregister).toHaveBeenCalled();
    expect(previewKeysPlugin.clear).toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(false);
  });

  it("no-ops for a remote device row", async () => {
    await enableNativePush(USER_ID);
    await unregisterIfCurrentDevice(await hashEndpoint("other-token"), USER_ID);
    expect(push.unregister).not.toHaveBeenCalled();
    expect(previewKeysPlugin.clear).not.toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(true);
  });
});

describe("clearPushPreviewKeys", () => {
  it("wipes the device keyring", async () => {
    await clearPushPreviewKeys();
    expect(previewKeysPlugin.clear).toHaveBeenCalled();
  });

  it("tolerates a shell lacking the plugin", async () => {
    previewKeysPlugin.clear.mockRejectedValue(new Error("not implemented"));
    await expect(clearPushPreviewKeys()).resolves.toBeUndefined();
  });

  it("is a no-op outside the native shell", async () => {
    isNative.mockReturnValue(false);
    await clearPushPreviewKeys();
    expect(previewKeysPlugin.clear).not.toHaveBeenCalled();
  });
});

describe("currentNativeTokenHash", () => {
  // The devices grid uses it to spot this device's row (hide "enable" when
  // already enrolled), so it must match the server-side token_hash exactly.
  it("matches the enrolled token's server hash", async () => {
    await enableNativePush(USER_ID);
    expect(await currentNativeTokenHash()).toBe(await hashEndpoint(TOKEN));
  });

  it("is null before any registration", async () => {
    expect(await currentNativeTokenHash()).toBeNull();
  });

  it("is null again after this device signs out", async () => {
    await enableNativePush(USER_ID);
    await unregisterIfCurrentDevice(await hashEndpoint(TOKEN), USER_ID);
    expect(await currentNativeTokenHash()).toBeNull();
  });
});

describe("pushTargetUrl", () => {
  // Mirrors targetUrl in public/sw.js — the two deep-link builders must route
  // a tap on the same payload to the same place.
  it("routes to the thread with the inbox filter and message anchor", () => {
    expect(
      pushTargetUrl({
        mailbox_id: "mb-1",
        thread_id: "th-1",
        message_id: "msg-1",
      }),
    ).toBe("/mailbox/mb-1/thread/th-1?has_active=1#thread-message-msg-1");
  });

  it("omits the anchor without a message id", () => {
    expect(pushTargetUrl({ mailbox_id: "mb-1", thread_id: "th-1" })).toBe(
      "/mailbox/mb-1/thread/th-1?has_active=1",
    );
  });

  it("falls back to the root without routing ids", () => {
    expect(pushTargetUrl({ thread_id: "th-1" })).toBe("/");
    expect(pushTargetUrl(undefined)).toBe("/");
  });
});

/**
 * The native push client is the iOS/Android counterpart of the Web Push flow:
 * these tests pin the registration contract (platform = transport, opt-in
 * marker gating, token-hash sign-out matching) and the tap deep-link mirroring
 * of the service worker's targetUrl.
 */
import { hashEndpoint, hasPushOptIn, setPushOptIn } from "@/features/push/shared";

// Partial mock: the api client pulled in through @/features/api/gen reaches
// auth-session.ts, which needs the real registerPlugin.
vi.mock("@capacitor/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@capacitor/core")>()),
  Capacitor: { getPlatform: vi.fn(), isNativePlatform: vi.fn() },
}));
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
  currentNativeTokenHash,
  enableNativePush,
  pushTargetUrl,
  refreshNativePushRegistration,
  unregisterIfCurrentDevice,
} from "./push";

import { isNativePlatform } from "./platform";

const USER_ID = "11111111-2222-3333-4444-555555555555";
const TOKEN = "apns-device-token-1";

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
});

describe("enableNativePush", () => {
  it("registers the APNs token as a push channel and stores the opt-in", async () => {
    await expect(enableNativePush(USER_ID)).resolves.toBe("registered");

    expect(createChannel).toHaveBeenCalledWith({
      type: "push",
      platform: "apns",
      token: TOKEN,
      name: "Mon iPhone",
      app_version: "1.2.0",
    });
    expect(hasPushOptIn(USER_ID)).toBe(true);
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
});

describe("unregisterIfCurrentDevice", () => {
  it("unregisters and clears the opt-in when the row is this device", async () => {
    await enableNativePush(USER_ID);
    await unregisterIfCurrentDevice(await hashEndpoint(TOKEN), USER_ID);
    expect(push.unregister).toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(false);
  });

  it("no-ops for a remote device row", async () => {
    await enableNativePush(USER_ID);
    await unregisterIfCurrentDevice(await hashEndpoint("other-token"), USER_ID);
    expect(push.unregister).not.toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(true);
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

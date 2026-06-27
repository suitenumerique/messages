/**
 * The security-sensitive Web Push client logic: the VAPID-rotation detection
 * (a rotated key silently kills every subscription — the backend can't see it),
 * and the shared-computer arbitration in `refreshWebPushSubscription` (a
 * leftover subscription must never keep alerting for a previous user, nor be
 * silently adopted by the next one).
 */
import { hashEndpoint, hasPushOptIn, setPushOptIn } from "@/features/push/shared";

// Repo pattern for the generated client: automock the resource submodule (the
// index re-exports it, so web-push.ts sees the mock through "@/features/api/gen").
vi.mock("@/features/api/gen/channels/channels");
vi.mock("@/features/api/utils", () => ({
  getApiOrigin: () => "https://api.test",
}));
// swUrl stamps the current language into the worker URL; the real instance
// would drag the http backend into the test environment.
vi.mock("@/features/i18n/initI18n", () => ({
  default: { resolvedLanguage: "en", language: "en" },
}));

import {
  usersMeChannelsCreate,
  usersMeChannelsList,
} from "@/features/api/gen";

import {
  currentWebPushTokenHash,
  isStaleForKey,
  refreshWebPushSubscription,
  unsubscribeIfCurrentBrowser,
  urlBase64ToUint8Array,
} from "./web-push";

const USER_ID = "11111111-2222-3333-4444-555555555555";

const b64url = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

/** Two distinct, well-formed 65-byte uncompressed P-256 points. */
const VAPID_KEY = b64url(new Uint8Array(65).map((_, i) => i));
const ROTATED_KEY = b64url(new Uint8Array(65).map((_, i) => 64 - i));

type FakeSubscription = {
  endpoint: string;
  options: { applicationServerKey: ArrayBuffer | null };
  toJSON: () => {
    endpoint: string;
    keys: { p256dh: string; auth: string };
  };
  unsubscribe: ReturnType<typeof vi.fn>;
};

const makeSubscription = ({
  endpoint = "https://push.example/ep-1",
  serverKey = VAPID_KEY,
}: { endpoint?: string; serverKey?: string | null } = {}): FakeSubscription => ({
  endpoint,
  options: {
    applicationServerKey: serverKey
      ? urlBase64ToUint8Array(serverKey).buffer
      : null,
  },
  toJSON: () => ({ endpoint, keys: { p256dh: "AAAA", auth: "BBBB" } }),
  unsubscribe: vi.fn().mockResolvedValue(true),
});

const asSubscription = (sub: FakeSubscription): PushSubscription =>
  sub as unknown as PushSubscription;

const registerDevice = vi.mocked(usersMeChannelsCreate);
const listDevices = vi.mocked(usersMeChannelsList);

/** A navigator.serviceWorker whose registration hands out `subscription` and
 * subscribes to `subscribed` afterwards. */
const stubServiceWorker = ({
  subscription = null as FakeSubscription | null,
  subscribed = makeSubscription(),
  registered = true,
} = {}) => {
  const pushManager = {
    getSubscription: vi.fn().mockResolvedValue(subscription),
    subscribe: vi.fn().mockResolvedValue(subscribed),
  };
  const registration = { pushManager };
  const serviceWorker = {
    getRegistration: vi.fn().mockResolvedValue(registered ? registration : null),
    register: vi.fn().mockResolvedValue(registration),
    ready: Promise.resolve(registration),
  };
  vi.stubGlobal("navigator", {
    serviceWorker,
    userAgent: "Mozilla/5.0 (Macintosh) Chrome/143.0.0.0 Safari/537.36",
  });
  return { pushManager, serviceWorker };
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // isWebPushSupported() gates every entry point on these three.
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal("Notification", { permission: "granted" });
  registerDevice.mockResolvedValue(
    {} as Awaited<ReturnType<typeof usersMeChannelsCreate>>,
  );
  listDevices.mockResolvedValue({
    data: [],
  } as unknown as Awaited<ReturnType<typeof usersMeChannelsList>>);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("urlBase64ToUint8Array", () => {
  it("decodes an unpadded base64url key to its 65 bytes", () => {
    const bytes = urlBase64ToUint8Array(VAPID_KEY);
    expect(bytes).toHaveLength(65);
    expect(bytes[0]).toBe(0);
    expect(bytes[64]).toBe(64);
  });

  it("accepts the standard-base64 alphabet the server may serve", () => {
    const standard = btoa(
      String.fromCharCode(...new Uint8Array(65).map((_, i) => i)),
    );
    expect(urlBase64ToUint8Array(standard)).toEqual(
      urlBase64ToUint8Array(VAPID_KEY),
    );
  });

  it("rejects anything that is not a 65-byte P-256 point", () => {
    expect(() => urlBase64ToUint8Array(b64url(new Uint8Array(64)))).toThrow(
      /65 bytes/,
    );
  });
});

describe("isStaleForKey", () => {
  it("matches the same key across base64 spellings (no spurious staleness)", () => {
    const sub = makeSubscription({ serverKey: VAPID_KEY });
    const padded = VAPID_KEY.replace(/-/g, "+").replace(/_/g, "/") + "=";
    expect(isStaleForKey(asSubscription(sub), padded)).toBe(false);
  });

  it("flags a subscription created under a rotated-away key", () => {
    const sub = makeSubscription({ serverKey: ROTATED_KEY });
    expect(isStaleForKey(asSubscription(sub), VAPID_KEY)).toBe(true);
  });

  it("keeps a subscription whose engine hides the key (can't prove a mismatch)", () => {
    const sub = makeSubscription({ serverKey: null });
    expect(isStaleForKey(asSubscription(sub), VAPID_KEY)).toBe(false);
  });
});

describe("currentWebPushTokenHash", () => {
  // The devices grid uses it to spot this device's row (hide "enable" when
  // already enrolled), so it must match the server-side token_hash exactly.
  it("hashes the live subscription's endpoint like the server does", async () => {
    const sub = makeSubscription();
    stubServiceWorker({ subscription: sub });
    expect(await currentWebPushTokenHash()).toBe(
      await hashEndpoint(sub.endpoint),
    );
  });

  it("is null without a live subscription", async () => {
    stubServiceWorker({ subscription: null });
    expect(await currentWebPushTokenHash()).toBeNull();
  });
});

describe("unsubscribeIfCurrentBrowser", () => {
  it("tears down this browser's subscription and drops the opt-in", async () => {
    const sub = makeSubscription();
    stubServiceWorker({ subscription: sub });
    setPushOptIn(USER_ID);

    await unsubscribeIfCurrentBrowser(await hashEndpoint(sub.endpoint), USER_ID);

    expect(sub.unsubscribe).toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(false);
  });

  it("no-ops for a remote device row", async () => {
    const sub = makeSubscription();
    stubServiceWorker({ subscription: sub });
    setPushOptIn(USER_ID);

    await unsubscribeIfCurrentBrowser(
      await hashEndpoint("https://push.example/other"),
      USER_ID,
    );

    expect(sub.unsubscribe).not.toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(true);
  });
});

describe("refreshWebPushSubscription", () => {
  it("stays passive when the user never enabled push here", async () => {
    const { serviceWorker } = stubServiceWorker({ registered: false });
    await refreshWebPushSubscription(VAPID_KEY, USER_ID);
    expect(serviceWorker.register).not.toHaveBeenCalled();
    expect(registerDevice).not.toHaveBeenCalled();
  });

  it("stays passive without the notification permission", async () => {
    vi.stubGlobal("Notification", { permission: "default" });
    const { serviceWorker } = stubServiceWorker();
    await refreshWebPushSubscription(VAPID_KEY, USER_ID);
    expect(serviceWorker.register).not.toHaveBeenCalled();
  });

  it("re-registers an opted-in user's live subscription", async () => {
    const sub = makeSubscription();
    stubServiceWorker({ subscription: sub });
    setPushOptIn(USER_ID);

    await refreshWebPushSubscription(VAPID_KEY, USER_ID);

    expect(registerDevice).toHaveBeenCalledWith(
      expect.objectContaining({ token: sub.endpoint }),
    );
    expect(sub.unsubscribe).not.toHaveBeenCalled();
  });

  it("re-subscribes an opted-in user whose subscription is gone", async () => {
    const fresh = makeSubscription({ endpoint: "https://push.example/fresh" });
    const { pushManager } = stubServiceWorker({
      subscription: null,
      subscribed: fresh,
    });
    setPushOptIn(USER_ID);

    await refreshWebPushSubscription(VAPID_KEY, USER_ID);

    expect(pushManager.subscribe).toHaveBeenCalled();
    expect(registerDevice).toHaveBeenCalledWith(
      expect.objectContaining({ token: fresh.endpoint }),
    );
  });

  it("adopts a marker-less subscription only on proven server ownership", async () => {
    // Legacy user (opted in before the marker existed): their device list
    // holds this endpoint's hash, so the refresh migrates them onto the marker.
    const sub = makeSubscription();
    stubServiceWorker({ subscription: sub });
    listDevices.mockResolvedValue({
      data: [{ type: "push", token_hash: await hashEndpoint(sub.endpoint) }],
    } as unknown as Awaited<ReturnType<typeof usersMeChannelsList>>);

    await refreshWebPushSubscription(VAPID_KEY, USER_ID);

    expect(registerDevice).toHaveBeenCalledWith(
      expect.objectContaining({ token: sub.endpoint }),
    );
    expect(hasPushOptIn(USER_ID)).toBe(true);
    expect(sub.unsubscribe).not.toHaveBeenCalled();
  });

  it("tears down a previous user's leftover on a shared computer", async () => {
    // No marker for this user and the server doesn't own the endpoint: the
    // subscription belongs to whoever used this browser before. Killing it
    // locally stops their alerts; it must never be re-registered as ours.
    const sub = makeSubscription();
    stubServiceWorker({ subscription: sub });

    await refreshWebPushSubscription(VAPID_KEY, USER_ID);

    expect(sub.unsubscribe).toHaveBeenCalled();
    expect(registerDevice).not.toHaveBeenCalled();
    expect(hasPushOptIn(USER_ID)).toBe(false);
  });

  it("re-subscribes with the current key after a VAPID rotation", async () => {
    const stale = makeSubscription({ serverKey: ROTATED_KEY });
    const fresh = makeSubscription({ endpoint: "https://push.example/fresh" });
    const { pushManager } = stubServiceWorker({
      subscription: stale,
      subscribed: fresh,
    });
    setPushOptIn(USER_ID);

    await refreshWebPushSubscription(VAPID_KEY, USER_ID);

    expect(stale.unsubscribe).toHaveBeenCalled();
    expect(pushManager.subscribe).toHaveBeenCalledWith(
      expect.objectContaining({
        applicationServerKey: urlBase64ToUint8Array(VAPID_KEY),
      }),
    );
    expect(registerDevice).toHaveBeenCalledWith(
      expect.objectContaining({ token: fresh.endpoint }),
    );
  });
});

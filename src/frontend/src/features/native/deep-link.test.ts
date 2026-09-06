/**
 * The dispatcher exists because @capacitor/app notifies `appUrlOpen` with
 * retainUntilConsumed: a link nobody listened to is kept by the native layer
 * and handed to the next subscriber. Subscribing per flow therefore serves the
 * previous flow's callback to the current one — these tests pin the routing
 * that makes that impossible.
 */
import type { App as AppType } from "@capacitor/app";

vi.mock("@capacitor/app", () => ({
  App: { addListener: vi.fn() },
}));
vi.mock("./platform", () => ({
  isNativePlatform: vi.fn(),
}));

type DeepLinkTestContext = {
  deepLink: typeof import("./deep-link");
  /** Fire an appUrlOpen event from the native layer. */
  emit: (url: string) => void;
  addListener: ReturnType<typeof vi.fn>;
};

const loadDeepLink = async (native = true): Promise<DeepLinkTestContext> => {
  vi.resetModules();
  const deepLink = await import("./deep-link");
  const { App } = await import("@capacitor/app");
  const { isNativePlatform } = await import("./platform");
  vi.mocked(isNativePlatform).mockReturnValue(native);

  const addListener = vi.mocked(App as unknown as typeof AppType)
    .addListener as unknown as ReturnType<typeof vi.fn>;
  addListener.mockResolvedValue({ remove: vi.fn() });

  return {
    deepLink,
    addListener,
    emit: (url: string) => {
      const handler = addListener.mock.calls.at(-1)?.[1] as (event: {
        url: string;
      }) => void;
      handler({ url });
    },
  };
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("native deep links", () => {
  it("routes links to the fallback when no flow is in flight", async () => {
    const ctx = await loadDeepLink();
    const fallback = vi.fn();
    ctx.deepLink.initNativeDeepLinks(fallback);

    ctx.emit("stmessages://auth?token=abc");

    expect(fallback).toHaveBeenCalledWith("stmessages://auth?token=abc");
  });

  it("routes links to the flow that captured them", async () => {
    const ctx = await loadDeepLink();
    const fallback = vi.fn();
    const flow = vi.fn();
    ctx.deepLink.initNativeDeepLinks(fallback);
    ctx.deepLink.captureDeepLinks(flow);

    ctx.emit("stmessages://auth?token=abc");

    expect(flow).toHaveBeenCalledWith("stmessages://auth?token=abc");
    expect(fallback).not.toHaveBeenCalled();
  });

  it("hands routing back to the fallback once the flow releases", async () => {
    const ctx = await loadDeepLink();
    const fallback = vi.fn();
    const flow = vi.fn();
    ctx.deepLink.initNativeDeepLinks(fallback);
    ctx.deepLink.captureDeepLinks(flow)();

    ctx.emit("stmessages://auth?token=abc");

    expect(flow).not.toHaveBeenCalled();
    expect(fallback).toHaveBeenCalledOnce();
  });

  it("keeps a stale release from stealing the current flow's links", async () => {
    const ctx = await loadDeepLink();
    ctx.deepLink.initNativeDeepLinks(vi.fn());
    const releaseFirst = ctx.deepLink.captureDeepLinks(vi.fn());
    const second = vi.fn();
    ctx.deepLink.captureDeepLinks(second);

    // The first flow settles late (its cancellation timer fires after the
    // user already started another attempt): releasing must be a no-op.
    releaseFirst();
    ctx.emit("stmessages://auth?token=abc");

    expect(second).toHaveBeenCalledWith("stmessages://auth?token=abc");
  });

  it("registers a single listener for the app lifetime", async () => {
    const ctx = await loadDeepLink();

    ctx.deepLink.initNativeDeepLinks(vi.fn());
    ctx.deepLink.initNativeDeepLinks(vi.fn());

    // A second listener would take its own copy of a retained link.
    expect(ctx.addListener).toHaveBeenCalledOnce();
  });

  it("stays out of the way on the web", async () => {
    const ctx = await loadDeepLink(false);

    ctx.deepLink.initNativeDeepLinks(vi.fn());

    expect(ctx.addListener).not.toHaveBeenCalled();
  });
});

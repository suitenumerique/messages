/**
 * The OTA client decides whether a fleet updates, holds or rolls back: these
 * tests pin the guards (freshness, downgrade, boot-loop) that keep a bad or
 * replayed manifest from breaking devices.
 *
 * The manifest URL is passed by the caller (resolved from /config), but the
 * hot-reload skip still reads import.meta.env at call time, so every test
 * stubs the env and imports a fresh module graph via `loadOta()`.
 */
import type { CapacitorHttp as CapacitorHttpType } from "@capacitor/core";
import type { CapacitorUpdater as CapacitorUpdaterType } from "@capgo/capacitor-updater";

vi.mock("@capacitor/core", () => ({
  CapacitorHttp: { get: vi.fn() },
}));
vi.mock("@capgo/capacitor-updater", () => ({
  CapacitorUpdater: {
    notifyAppReady: vi.fn(),
    current: vi.fn(),
    getFailedUpdate: vi.fn(),
    download: vi.fn(),
    set: vi.fn(),
  },
}));
vi.mock("./platform", () => ({
  isNativePlatform: vi.fn(),
}));

const MANIFEST_URL = "http://ota.test/channels/dev/manifest.json";

type OtaTestContext = {
  ota: typeof import("./ota");
  http: { get: ReturnType<typeof vi.fn> };
  updater: Record<
    "notifyAppReady" | "current" | "getFailedUpdate" | "download" | "set",
    ReturnType<typeof vi.fn>
  >;
  isNative: ReturnType<typeof vi.fn>;
};

const loadOta = async (): Promise<OtaTestContext> => {
  vi.resetModules();
  // Stub unconditionally: a developer's frontend.local (hot reload enabled) or
  // shell env would otherwise leak into import.meta.env and flip these branches.
  vi.stubEnv("MOBILE_DEV_SERVER_URL", "");
  // Baked verification key: without it checkAndApplyOtaUpdate refuses to
  // apply anything (see the key-less refusal test, which re-stubs it empty).
  vi.stubEnv("MOBILE_OTA_SIGNING_PUBLIC_KEY_B64", "test-public-key");
  // Re-import everything in the same fresh module graph so the mock instances
  // observed here are the ones the ota module holds. SEQUENTIALLY: concurrent
  // dynamic imports right after resetModules race and can instantiate a mocked
  // module twice, silently splitting the test's instance from the ota one.
  const ota = await import("./ota");
  const { CapacitorHttp } = await import("@capacitor/core");
  const { CapacitorUpdater } = await import("@capgo/capacitor-updater");
  const { isNativePlatform } = await import("./platform");
  const isNative = vi.mocked(isNativePlatform);
  isNative.mockReturnValue(true);
  return {
    ota,
    http: vi.mocked(CapacitorHttp as unknown as typeof CapacitorHttpType),
    updater: vi.mocked(
      CapacitorUpdater as unknown as typeof CapacitorUpdaterType,
    ) as unknown as OtaTestContext["updater"],
    isNative,
  };
};

/** Wire the standard happy-path plumbing, overridable per test. */
const primeUpdate = (
  ctx: OtaTestContext,
  {
    current,
    manifest,
    bootFailed = null,
  }: {
    current: string;
    manifest: { version: string; url?: string; checksum?: string; sessionKey?: string };
    bootFailed?: string | null;
  },
) => {
  ctx.http.get.mockResolvedValue({ data: { url: "http://ota.test/bundle.zip", ...manifest } });
  ctx.updater.current.mockResolvedValue({ bundle: { version: current } });
  ctx.updater.getFailedUpdate.mockResolvedValue(
    bootFailed ? { bundle: { version: bootFailed } } : null,
  );
  ctx.updater.download.mockResolvedValue({ id: "next-bundle" });
  ctx.updater.set.mockResolvedValue(undefined);
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  // The boot-loop guard mirrors the plugin's failed-update record here; drop
  // it so a blacklist written by one test never leaks into the next.
  localStorage.clear();
  // Restore the console spies installed by the error/guard tests so a silenced
  // console never leaks into an unrelated test.
  vi.restoreAllMocks();
});

describe("notifyOtaAppReady", () => {
  it("confirms the running bundle on native", async () => {
    const ctx = await loadOta();
    await ctx.ota.notifyOtaAppReady();
    expect(ctx.updater.notifyAppReady).toHaveBeenCalledOnce();
  });

  it("does nothing on the web", async () => {
    const ctx = await loadOta();
    ctx.isNative.mockReturnValue(false);
    await ctx.ota.notifyOtaAppReady();
    expect(ctx.updater.notifyAppReady).not.toHaveBeenCalled();
  });

  it("swallows plugin failures", async () => {
    const ctx = await loadOta();
    const error = new Error("bridge down");
    ctx.updater.notifyAppReady.mockRejectedValue(error);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(ctx.ota.notifyOtaAppReady()).resolves.toBeUndefined();
    expect(consoleError).toHaveBeenCalledWith("OTA notifyAppReady failed", error);
  });
});

describe("checkAndApplyOtaUpdate", () => {
  it("does nothing on the web", async () => {
    const ctx = await loadOta();
    ctx.isNative.mockReturnValue(false);
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.http.get).not.toHaveBeenCalled();
  });

  it("does nothing when no manifest URL is configured", async () => {
    const ctx = await loadOta();
    await ctx.ota.checkAndApplyOtaUpdate(undefined);
    expect(ctx.http.get).not.toHaveBeenCalled();
  });

  it("refuses a manifest URL when the build embeds no verification key", async () => {
    const ctx = await loadOta();
    vi.stubEnv("MOBILE_OTA_SIGNING_PUBLIC_KEY_B64", "");
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.http.get).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest URL configured but this build embeds no signing public " +
        "key (MOBILE_OTA_SIGNING_PUBLIC_KEY_B64); skipping unverifiable update.",
    );
  });

  it("skips a manifest advertising the running version", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-aaa", manifest: { version: "100-aaa" } });
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
  });

  it("refuses a manifest with a lower count (downgrade/replay guard)", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-bbb", manifest: { version: "99-aaa" } });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest 99-aaa is not newer than the running 100-bbb; skipping to avoid a downgrade.",
    );
  });

  it("refuses an equal count from a diverged branch", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-bbb", manifest: { version: "100-aaa" } });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest 100-aaa is not newer than the running 100-bbb; skipping to avoid a downgrade.",
    );
  });

  it("refuses a version that previously failed to boot (boot-loop guard)", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb" },
      bootFailed: "101-bbb",
    });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA 101-bbb previously failed to boot; skipping.",
    );
  });

  it("keeps refusing a boot-failed version after the plugin record self-clears", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb" },
      bootFailed: "101-bbb",
    });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);

    // getFailedUpdate() clears the native record on read: from now on it
    // resolves null and only the localStorage mirror remembers the failure.
    ctx.updater.getFailedUpdate.mockResolvedValue(null);
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledTimes(2);
  });

  it("still applies a newer version after an older one failed to boot", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "102-ccc" },
      bootFailed: "101-bbb",
    });
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.set).toHaveBeenCalledWith({ id: "next-bundle" });
  });

  it("downloads and activates a strictly newer bundle", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb", checksum: "chk", sessionKey: "sk" },
    });
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);

    expect(ctx.http.get).toHaveBeenCalledWith({ url: MANIFEST_URL });
    expect(ctx.updater.download).toHaveBeenCalledWith({
      url: "http://ota.test/bundle.zip",
      version: "101-bbb",
      // Both must reach the native layer or signature verification is skipped.
      checksum: "chk",
      sessionKey: "sk",
    });
    expect(ctx.updater.set).toHaveBeenCalledWith({ id: "next-bundle" });
  });

  it("falls back to a plain inequality check for non-hybrid ids", async () => {
    const ctx = await loadOta();
    // A fresh store install without MOBILE_OTA_BUILD_ID reports the literal
    // "builtin": no ordering is possible, any different version applies.
    primeUpdate(ctx, { current: "builtin", manifest: { version: "100-aaa" } });
    await ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.set).toHaveBeenCalled();
  });

  it("leaves the current bundle untouched when the check fails", async () => {
    const ctx = await loadOta();
    const error = new Error("bucket unreachable");
    ctx.http.get.mockRejectedValue(error);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(ctx.ota.checkAndApplyOtaUpdate(MANIFEST_URL)).resolves.toBeUndefined();
    expect(ctx.updater.set).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith("OTA update check failed", error);
  });
});

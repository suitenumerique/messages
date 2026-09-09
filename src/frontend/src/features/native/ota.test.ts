/**
 * The OTA client decides whether a fleet updates, holds or rolls back: these
 * tests pin the guards (freshness, downgrade, boot-loop, sequence floor) that
 * keep a bad or replayed manifest from breaking devices, and the staging flow
 * (next(), never a mid-session reload) the update toast builds on.
 *
 * The manifest URL is passed by the caller (resolved from /config), but the
 * hot-reload skip still reads import.meta.env at call time, so every test
 * stubs the env and imports a fresh module graph via `loadOta()`.
 */
import type { App as AppType } from "@capacitor/app";
import type { CapacitorHttp as CapacitorHttpType } from "@capacitor/core";
import type { CapacitorUpdater as CapacitorUpdaterType } from "@capgo/capacitor-updater";

vi.mock("@capacitor/app", () => ({
  App: { addListener: vi.fn() },
}));
vi.mock("@capacitor/core", () => ({
  CapacitorHttp: { get: vi.fn() },
}));
vi.mock("@capgo/capacitor-updater", () => ({
  CapacitorUpdater: {
    notifyAppReady: vi.fn(),
    current: vi.fn(),
    getFailedUpdate: vi.fn(),
    download: vi.fn(),
    list: vi.fn(),
    next: vi.fn(),
    reload: vi.fn(),
    set: vi.fn(),
  },
}));
vi.mock("./platform", () => ({
  isNativePlatform: vi.fn(),
}));

const MANIFEST_URL = "http://ota.test/channels/dev/manifest.json";
const SEQUENCE_KEY = "ota-applied-sequence";

type OtaTestContext = {
  ota: typeof import("./ota");
  http: { get: ReturnType<typeof vi.fn> };
  updater: Record<
    | "notifyAppReady"
    | "current"
    | "getFailedUpdate"
    | "download"
    | "list"
    | "next"
    | "reload"
    | "set",
    ReturnType<typeof vi.fn>
  >;
  app: { addListener: ReturnType<typeof vi.fn> };
  isNative: ReturnType<typeof vi.fn>;
};

const loadOta = async (): Promise<OtaTestContext> => {
  vi.resetModules();
  // Stub unconditionally: a developer's frontend.local (hot reload enabled) or
  // shell env would otherwise leak into import.meta.env and flip these branches.
  vi.stubEnv("MOBILE_DEV_SERVER_URL", "");
  // Baked verification key: without it checkAndStageOtaUpdate refuses to
  // apply anything (see the key-less refusal test, which re-stubs it empty).
  vi.stubEnv("MOBILE_OTA_SIGNING_PUBLIC_KEY_B64", "test-public-key");
  // Re-import everything in the same fresh module graph so the mock instances
  // observed here are the ones the ota module holds. SEQUENTIALLY: concurrent
  // dynamic imports right after resetModules race and can instantiate a mocked
  // module twice, silently splitting the test's instance from the ota one.
  const ota = await import("./ota");
  const { App } = await import("@capacitor/app");
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
    app: vi.mocked(App as unknown as typeof AppType) as unknown as {
      addListener: ReturnType<typeof vi.fn>;
    },
    isNative,
  };
};

/** Wire the standard happy-path plumbing, overridable per test. */
const primeUpdate = (
  ctx: OtaTestContext,
  {
    current,
    native = "builtin",
    manifest,
    bootFailed = null,
    localBundles = [],
  }: {
    current: string;
    native?: string;
    manifest: {
      version: string;
      url?: string;
      checksum?: string;
      sessionKey?: string;
      sequence?: number;
    };
    bootFailed?: string | null;
    localBundles?: { id: string; version: string; status: string }[];
  },
) => {
  ctx.http.get.mockResolvedValue({ data: { url: "http://ota.test/bundle.zip", ...manifest } });
  ctx.updater.current.mockResolvedValue({ bundle: { version: current }, native });
  ctx.updater.getFailedUpdate.mockResolvedValue(
    bootFailed ? { bundle: { version: bootFailed } } : null,
  );
  ctx.updater.list.mockResolvedValue({ bundles: localBundles });
  ctx.updater.download.mockResolvedValue({ id: "next-bundle", version: manifest.version });
  ctx.updater.next.mockResolvedValue(undefined);
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  // The boot-loop guard and the sequence floor persist here; drop them so a
  // value written by one test never leaks into the next.
  localStorage.clear();
  // Restore the console spies installed by the error/guard tests so a silenced
  // console never leaks into an unrelated test.
  vi.restoreAllMocks();
  vi.useRealTimers();
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

describe("checkAndStageOtaUpdate", () => {
  it("does nothing on the web", async () => {
    const ctx = await loadOta();
    ctx.isNative.mockReturnValue(false);
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.http.get).not.toHaveBeenCalled();
  });

  it("does nothing when no manifest URL is configured", async () => {
    const ctx = await loadOta();
    await ctx.ota.checkAndStageOtaUpdate(undefined);
    expect(ctx.http.get).not.toHaveBeenCalled();
  });

  it("refuses a manifest URL when the build embeds no verification key", async () => {
    const ctx = await loadOta();
    vi.stubEnv("MOBILE_OTA_SIGNING_PUBLIC_KEY_B64", "");
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.http.get).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest URL configured but this build embeds no signing public " +
        "key (MOBILE_OTA_SIGNING_PUBLIC_KEY_B64); skipping unverifiable update.",
    );
  });

  it("skips a manifest advertising the running version", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-aaa", manifest: { version: "100-aaa" } });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
  });

  it("advances the sequence floor when already on the advertised version", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "100-aaa", sequence: 7 },
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(localStorage.getItem(SEQUENCE_KEY)).toBe("7");
  });

  it("refuses a legacy manifest with a lower count (downgrade/replay guard)", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-bbb", manifest: { version: "99-aaa" } });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest 99-aaa is not newer than the running 100-bbb; skipping to avoid a downgrade.",
    );
  });

  it("refuses a legacy equal count from a diverged branch", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-bbb", manifest: { version: "100-aaa" } });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest 100-aaa is not newer than the running 100-bbb; skipping to avoid a downgrade.",
    );
  });

  it("follows a rollback: lower count but higher sequence", async () => {
    const ctx = await loadOta();
    localStorage.setItem(SEQUENCE_KEY, "42");
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: { version: "99-aaa", sequence: 43 },
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.next).toHaveBeenCalledWith({ id: "next-bundle" });
    expect(localStorage.getItem(SEQUENCE_KEY)).toBe("43");
  });

  it("refuses a sequenced manifest at or below the persisted floor", async () => {
    const ctx = await loadOta();
    localStorage.setItem(SEQUENCE_KEY, "42");
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: { version: "99-aaa", sequence: 42 },
    });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest sequence 42 is not newer than the applied 42; skipping stale manifest.",
    );
  });

  it("accepts a sequenced rollback with no persisted floor (trust-on-first-use)", async () => {
    const ctx = await loadOta();
    // A device that predates sequences (or lost its storage) must still
    // follow a rollback below its running count — that is the migration path.
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: { version: "99-aaa", sequence: 43 },
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.next).toHaveBeenCalledWith({ id: "next-bundle" });
    expect(localStorage.getItem(SEQUENCE_KEY)).toBe("43");
  });

  it("never stages a bundle older than the native build (native floor)", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-bbb",
      native: "100-bbb",
      manifest: { version: "99-aaa", sequence: 43 },
    });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest 99-aaa is older than the native build 100-bbb; skipping.",
    );
  });

  it("treats a malformed sequence as a legacy manifest", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: {
        version: "99-aaa",
        sequence: "43" as unknown as number,
      },
    });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA manifest 99-aaa is not newer than the running 100-bbb; skipping to avoid a downgrade.",
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
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
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
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);

    // getFailedUpdate() clears the native record on read: from now on it
    // resolves null and only the localStorage mirror remembers the failure.
    ctx.updater.getFailedUpdate.mockResolvedValue(null);
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledTimes(2);
  });

  it("lets the boot-loop guard win over a rollback pointing at the failed version", async () => {
    const ctx = await loadOta();
    // Re-staging a version this device already boot-looped on would just crash
    // and revert again; such devices wait for a new forward publish.
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: { version: "99-aaa", sequence: 43 },
      bootFailed: "99-aaa",
    });
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalledWith(
      "OTA 99-aaa previously failed to boot; skipping.",
    );
  });

  it("still applies a newer version after an older one failed to boot", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "102-ccc" },
      bootFailed: "101-bbb",
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.next).toHaveBeenCalledWith({ id: "next-bundle" });
  });

  it("downloads and stages a strictly newer bundle without reloading", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb", checksum: "chk", sessionKey: "sk" },
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);

    expect(ctx.http.get).toHaveBeenCalledWith({ url: MANIFEST_URL });
    expect(ctx.updater.download).toHaveBeenCalledWith({
      url: "http://ota.test/bundle.zip",
      version: "101-bbb",
      // Both must reach the native layer or signature verification is skipped.
      checksum: "chk",
      sessionKey: "sk",
    });
    expect(ctx.updater.next).toHaveBeenCalledWith({ id: "next-bundle" });
    // Staging must never interrupt the session the way set() used to.
    expect(ctx.updater.set).not.toHaveBeenCalled();
    expect(ctx.updater.reload).not.toHaveBeenCalled();
  });

  it("reuses a fully installed local copy of the target version", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: { version: "99-aaa", sequence: 43 },
      localBundles: [
        { id: "local-old", version: "99-aaa", status: "success" },
        { id: "local-other", version: "98-zzz", status: "success" },
      ],
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).not.toHaveBeenCalled();
    expect(ctx.updater.next).toHaveBeenCalledWith({ id: "local-old" });
  });

  it("re-downloads when the local copy is not fully installed", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-bbb",
      manifest: { version: "99-aaa", sequence: 43 },
      localBundles: [{ id: "local-broken", version: "99-aaa", status: "error" }],
    });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.download).toHaveBeenCalled();
    expect(ctx.updater.next).toHaveBeenCalledWith({ id: "next-bundle" });
  });

  it("notifies staged-update subscribers, replaying for late ones", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb", sequence: 5 },
    });
    const early = vi.fn();
    ctx.ota.subscribeOtaUpdateStaged(early);
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(early).toHaveBeenCalledWith({ id: "next-bundle", version: "101-bbb" });

    // The toast hook mounts after the boot check finished: it must still learn
    // about the staged bundle.
    const late = vi.fn();
    ctx.ota.subscribeOtaUpdateStaged(late);
    expect(late).toHaveBeenCalledWith({ id: "next-bundle", version: "101-bbb" });
  });

  it("does not persist the sequence when staging fails", async () => {
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb", sequence: 5 },
    });
    const error = new Error("no disk space");
    ctx.updater.download.mockRejectedValue(error);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const subscriber = vi.fn();
    ctx.ota.subscribeOtaUpdateStaged(subscriber);
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    // The release stays retryable: floor untouched, no staged notification.
    expect(localStorage.getItem(SEQUENCE_KEY)).toBeNull();
    expect(subscriber).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith("OTA update check failed", error);
  });

  it("falls back to a plain inequality check for non-hybrid ids", async () => {
    const ctx = await loadOta();
    // A fresh store install without MOBILE_OTA_BUILD_ID reports the literal
    // "builtin": no ordering is possible, any different version applies.
    primeUpdate(ctx, { current: "builtin", manifest: { version: "100-aaa" } });
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.next).toHaveBeenCalled();
  });

  it("leaves the current bundle untouched when the check fails", async () => {
    const ctx = await loadOta();
    const error = new Error("bucket unreachable");
    ctx.http.get.mockRejectedValue(error);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL)).resolves.toBeUndefined();
    expect(ctx.updater.next).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith("OTA update check failed", error);
  });
});

describe("applyStagedOtaUpdate", () => {
  it("reloads onto the staged bundle", async () => {
    const ctx = await loadOta();
    ctx.updater.reload.mockResolvedValue(undefined);
    await ctx.ota.applyStagedOtaUpdate();
    expect(ctx.updater.reload).toHaveBeenCalledOnce();
  });

  it("swallows reload failures", async () => {
    const ctx = await loadOta();
    const error = new Error("no pending bundle");
    ctx.updater.reload.mockRejectedValue(error);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(ctx.ota.applyStagedOtaUpdate()).resolves.toBeUndefined();
    expect(consoleError).toHaveBeenCalledWith("OTA reload failed", error);
  });
});

describe("listenForOtaUpdatesOnResume", () => {
  const registerResume = (ctx: OtaTestContext): (() => void) => {
    ctx.ota.listenForOtaUpdatesOnResume(MANIFEST_URL);
    expect(ctx.app.addListener).toHaveBeenCalledWith(
      "resume",
      expect.any(Function),
    );
    return ctx.app.addListener.mock.calls[0][1] as () => void;
  };

  it("does nothing on the web or without a manifest URL", async () => {
    const ctx = await loadOta();
    ctx.isNative.mockReturnValue(false);
    ctx.ota.listenForOtaUpdatesOnResume(MANIFEST_URL);
    ctx.isNative.mockReturnValue(true);
    ctx.ota.listenForOtaUpdatesOnResume(undefined);
    expect(ctx.app.addListener).not.toHaveBeenCalled();
  });

  it("checks on foreground, at most once per throttle window", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-11T10:00:00Z"));
    const ctx = await loadOta();
    primeUpdate(ctx, { current: "100-aaa", manifest: { version: "100-aaa" } });
    const onResume = registerResume(ctx);

    onResume();
    await vi.runAllTimersAsync();
    expect(ctx.http.get).toHaveBeenCalledTimes(1);

    // 10 minutes later: inside the 30 min throttle window, no new check.
    vi.advanceTimersByTime(10 * 60 * 1000);
    onResume();
    await vi.runAllTimersAsync();
    expect(ctx.http.get).toHaveBeenCalledTimes(1);

    // 35 more minutes: the window elapsed, the check runs again.
    vi.advanceTimersByTime(35 * 60 * 1000);
    onResume();
    await vi.runAllTimersAsync();
    expect(ctx.http.get).toHaveBeenCalledTimes(2);
  });

  it("stays idle while an update is already staged", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-11T10:00:00Z"));
    const ctx = await loadOta();
    primeUpdate(ctx, {
      current: "100-aaa",
      manifest: { version: "101-bbb", sequence: 5 },
    });
    const onResume = registerResume(ctx);

    // Boot check stages the update…
    await ctx.ota.checkAndStageOtaUpdate(MANIFEST_URL);
    expect(ctx.updater.next).toHaveBeenCalledOnce();

    // …after which foregrounds re-check nothing: the staged bundle applies at
    // the very next background anyway (Capgo next() semantics).
    vi.advanceTimersByTime(60 * 60 * 1000);
    onResume();
    await vi.runAllTimersAsync();
    expect(ctx.http.get).toHaveBeenCalledTimes(1);
  });
});

/**
 * The mobile login hands a Django session over through a deep link, and the
 * whole flow runs while the app is in the *background* — the system browser is
 * on top. These tests pin what that costs: the flow may lose its JS context
 * mid-air (a staged OTA bundle installs on background and reloads the WebView,
 * or Android reclaims the process), and the callback then comes back to an app
 * that never opened the browser. What used to happen was worse than a failed
 * login: the unconsumed link was replayed into the *next* attempt, which then
 * exchanged an expired token, so every retry failed until the app was killed.
 *
 * So: the PKCE verifier must outlive the context that generated it, and an
 * auth link must never be left dangling for a later attempt to pick up.
 */
import { APP_STORAGE_PREFIX } from "@/features/config/constants";

vi.mock("./auth-session", () => ({
  openAuthSession: vi.fn(),
}));
vi.mock("./csrf", () => ({
  getNativeCsrfToken: vi.fn(),
  setNativeCsrfToken: vi.fn(),
}));
vi.mock("./fetch", () => ({
  nativeFetch: vi.fn(),
}));
vi.mock("@capacitor/core", () => ({
  Capacitor: { isNativePlatform: () => true },
  CapacitorCookies: { clearAllCookies: vi.fn() },
}));

const PENDING_LOGIN_KEY = `${APP_STORAGE_PREFIX}native-login-pending`;
const CALLBACK = "stmessages://auth?token=one-time-token";

type AuthTestContext = {
  auth: typeof import("./auth");
  openAuthSession: ReturnType<typeof vi.fn>;
  setNativeCsrfToken: ReturnType<typeof vi.fn>;
  getNativeCsrfToken: ReturnType<typeof vi.fn>;
  nativeFetch: ReturnType<typeof vi.fn>;
  clearAllCookies: ReturnType<typeof vi.fn>;
  fetchMock: ReturnType<typeof vi.fn>;
  reload: ReturnType<typeof vi.fn>;
};

const loadAuth = async (): Promise<AuthTestContext> => {
  vi.resetModules();
  // The scheme is read from the build env at module load (see auth.ts).
  vi.stubEnv("MOBILE_AUTH_SCHEME", "stmessages");
  const auth = await import("./auth");
  const { openAuthSession } = await import("./auth-session");
  const { getNativeCsrfToken, setNativeCsrfToken } = await import("./csrf");
  const { nativeFetch } = await import("./fetch");
  const { CapacitorCookies } = await import("@capacitor/core");

  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ csrf_token: "csrf-from-exchange" }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const reload = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload });

  return {
    auth,
    openAuthSession: vi.mocked(openAuthSession),
    setNativeCsrfToken: vi.mocked(setNativeCsrfToken),
    getNativeCsrfToken: vi.mocked(getNativeCsrfToken),
    nativeFetch: vi.mocked(nativeFetch),
    clearAllCookies: vi.mocked(CapacitorCookies.clearAllCookies),
    fetchMock,
    reload,
  };
};

const exchangeBody = (fetchMock: ReturnType<typeof vi.fn>) =>
  JSON.parse(fetchMock.mock.calls[0][1].body as string) as {
    token: string;
    code_verifier: string;
  };

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("nativeLogin", () => {
  it("exchanges the callback token for a session and reloads", async () => {
    const ctx = await loadAuth();
    ctx.openAuthSession.mockResolvedValue(CALLBACK);

    await ctx.auth.nativeLogin();

    expect(exchangeBody(ctx.fetchMock).token).toBe("one-time-token");
    expect(ctx.setNativeCsrfToken).toHaveBeenCalledWith("csrf-from-exchange");
    expect(ctx.reload).toHaveBeenCalled();
    // Nothing left behind: the token was single-use anyway.
    expect(localStorage.getItem(PENDING_LOGIN_KEY)).toBeNull();
  });

  it("persists the PKCE verifier before opening the browser", async () => {
    const ctx = await loadAuth();
    let persistedWhileOpen: string | null = null;
    ctx.openAuthSession.mockImplementation(() => {
      // Stands in for the whole time the app spends backgrounded: whatever
      // happens to this JS context, the verifier has to be recoverable.
      persistedWhileOpen = localStorage.getItem(PENDING_LOGIN_KEY);
      return Promise.resolve(CALLBACK);
    });

    await ctx.auth.nativeLogin();

    expect(persistedWhileOpen).not.toBeNull();
    const sent = exchangeBody(ctx.fetchMock).code_verifier;
    expect(
      (JSON.parse(persistedWhileOpen!) as { codeVerifier: string }).codeVerifier,
    ).toBe(sent);
  });

  it("drops the pending login when the flow is cancelled", async () => {
    const ctx = await loadAuth();
    // Same fresh module graph as the auth module under test (resetModules),
    // or the instanceof check on the other side never matches.
    const { AuthSessionCancelledError } = await import("./auth-errors");
    ctx.openAuthSession.mockRejectedValue(new AuthSessionCancelledError());

    await ctx.auth.nativeLogin();

    expect(ctx.fetchMock).not.toHaveBeenCalled();
    // A cancelled attempt must not leave a record a later stray deep link
    // could complete.
    expect(localStorage.getItem(PENDING_LOGIN_KEY)).toBeNull();
  });

  it("clears the pending login and rejects when the exchange is refused", async () => {
    const ctx = await loadAuth();
    ctx.openAuthSession.mockResolvedValue(CALLBACK);
    ctx.fetchMock.mockResolvedValue({ ok: false, status: 403 });

    // Unlike a cancellation, a refused exchange is a failure the login
    // screen must report.
    await expect(ctx.auth.nativeLogin()).rejects.toThrow("403");

    expect(ctx.reload).not.toHaveBeenCalled();
    expect(localStorage.getItem(PENDING_LOGIN_KEY)).toBeNull();
  });
});

describe("resumeNativeLogin", () => {
  /** Start a login, then throw the flow away as a lost JS context would. */
  const startAndAbandonLogin = async (ctx: AuthTestContext) => {
    ctx.openAuthSession.mockReturnValue(new Promise(() => undefined));
    void ctx.auth.nativeLogin();
    await vi.waitFor(() =>
      expect(localStorage.getItem(PENDING_LOGIN_KEY)).not.toBeNull(),
    );
    return JSON.parse(localStorage.getItem(PENDING_LOGIN_KEY)!) as {
      codeVerifier: string;
    };
  };

  it("completes a login whose context was lost while backgrounded", async () => {
    const first = await loadAuth();
    const { codeVerifier } = await startAndAbandonLogin(first);

    // The WebView reloaded (staged OTA bundle) while the user was on the IdP:
    // fresh module graph, and the deep link the native layer retained is
    // replayed to the boot fallback.
    const rebooted = await loadAuth();
    rebooted.auth.resumeNativeLogin(CALLBACK);

    await vi.waitFor(() => expect(rebooted.reload).toHaveBeenCalled());
    expect(exchangeBody(rebooted.fetchMock)).toEqual({
      token: "one-time-token",
      code_verifier: codeVerifier,
    });
  });

  it("ignores a callback with no pending login", async () => {
    const ctx = await loadAuth();

    ctx.auth.resumeNativeLogin(CALLBACK);

    // Dropping it is the point: an unconsumed link is what the native layer
    // replays into the next attempt.
    expect(ctx.fetchMock).not.toHaveBeenCalled();
    expect(ctx.reload).not.toHaveBeenCalled();
  });

  it("ignores a pending login older than its TTL", async () => {
    const ctx = await loadAuth();
    await startAndAbandonLogin(ctx);
    const stale = JSON.parse(localStorage.getItem(PENDING_LOGIN_KEY)!) as {
      codeVerifier: string;
      startedAt: number;
    };
    stale.startedAt -= 16 * 60 * 1000;
    localStorage.setItem(PENDING_LOGIN_KEY, JSON.stringify(stale));

    ctx.auth.resumeNativeLogin(CALLBACK);

    expect(ctx.fetchMock).not.toHaveBeenCalled();
    expect(localStorage.getItem(PENDING_LOGIN_KEY)).toBeNull();
  });

  it("ignores deep links that are not auth callbacks", async () => {
    const ctx = await loadAuth();
    await startAndAbandonLogin(ctx);

    ctx.auth.resumeNativeLogin("stmessages://logout");

    expect(ctx.fetchMock).not.toHaveBeenCalled();
    // The logout link belongs to another flow: the pending login stays.
    expect(localStorage.getItem(PENDING_LOGIN_KEY)).not.toBeNull();
  });

  it("does not reload when the resumed exchange fails", async () => {
    const ctx = await loadAuth();
    await startAndAbandonLogin(ctx);
    ctx.fetchMock.mockResolvedValue({ ok: false, status: 403 });

    ctx.auth.resumeNativeLogin(CALLBACK);

    await vi.waitFor(() => expect(ctx.fetchMock).toHaveBeenCalled());
    expect(ctx.reload).not.toHaveBeenCalled();
    expect(localStorage.getItem(PENDING_LOGIN_KEY)).toBeNull();
  });
});

describe("nativeLogout", () => {
  it("flushes the app session through nativeFetch with the CSRF headers", async () => {
    const ctx = await loadAuth();
    ctx.openAuthSession.mockResolvedValue("stmessages://logout");
    ctx.getNativeCsrfToken.mockReturnValue("csrf-native");
    ctx.nativeFetch.mockResolvedValue({ ok: true, status: 204 });

    await ctx.auth.nativeLogout();

    // The view enforces CSRF: the request needs the token AND the Origin
    // header, which only survives the bridge through nativeFetch (a plain
    // fetch would come back 403 and leave the server session alive).
    expect(ctx.fetchMock).not.toHaveBeenCalled();
    expect(ctx.nativeFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1.0/mobile/auth/logout/"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-CSRFToken": "csrf-native",
          Origin: expect.any(String),
        }),
      }),
    );
    expect(ctx.clearAllCookies).toHaveBeenCalled();
    expect(ctx.setNativeCsrfToken).toHaveBeenCalledWith(null);
    expect(ctx.reload).toHaveBeenCalled();
  });

  it("still ends the app session locally when the server flush is refused", async () => {
    const ctx = await loadAuth();
    ctx.openAuthSession.mockRejectedValue(new Error("sheet dismissed"));
    ctx.nativeFetch.mockResolvedValue({ ok: false, status: 403 });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await ctx.auth.nativeLogout();

    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("Mobile session logout refused (403)"),
    );
    expect(ctx.clearAllCookies).toHaveBeenCalled();
    expect(ctx.setNativeCsrfToken).toHaveBeenCalledWith(null);
    expect(ctx.reload).toHaveBeenCalled();
  });
});

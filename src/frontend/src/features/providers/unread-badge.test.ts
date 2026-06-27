import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/providers/theme-favicons", () => ({
    setFaviconBadge: vi.fn(),
}));

const SAFARI_UA =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5 Safari/605.1.15";
const CHROME_UA =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36";
const IOS_CHROME_UA =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/143.0.0.0 Mobile/15E148 Safari/604.1";
const FIREFOX_UA =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:145.0) Gecko/20100101 Firefox/145.0";

const stubUserAgent = (userAgent: string) =>
    vi.stubGlobal("navigator", { userAgent });

describe("unread-badge", () => {
    // The badge state lives in module scope, so every test re-imports the
    // module to start from a clean slate.
    let badge: typeof import("./unread-badge");
    let themeFavicons: typeof import("./theme-favicons");

    beforeEach(async () => {
        vi.resetModules();
        badge = await import("./unread-badge");
        themeFavicons = await import("./theme-favicons");
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.clearAllMocks();
    });

    it("drives the favicon dot and notifies subscribers", () => {
        const listener = vi.fn();
        badge.subscribeUnreadBadge(listener);

        badge.setUnreadBadge(true);

        expect(themeFavicons.setFaviconBadge).toHaveBeenCalledWith(true);
        expect(listener).toHaveBeenCalledTimes(1);
        expect(badge.getUnreadBadge()).toBe(true);
    });

    it("ignores a write that does not change the state", () => {
        const listener = vi.fn();
        badge.subscribeUnreadBadge(listener);

        badge.setUnreadBadge(true);
        badge.setUnreadBadge(true);

        expect(listener).toHaveBeenCalledTimes(1);
    });

    it("stops notifying an unsubscribed listener", () => {
        const listener = vi.fn();
        badge.subscribeUnreadBadge(listener)();

        badge.setUnreadBadge(true);

        expect(listener).not.toHaveBeenCalled();
    });

    // WebKit never re-reads the favicon after first paint, so the title is the
    // only surface left to carry the badge there. Everywhere else the favicon
    // dot does it, and a title marker would only duplicate it.
    it.each([
        ["Safari", SAFARI_UA, "• "],
        ["iOS Chrome (WebKit underneath)", IOS_CHROME_UA, "• "],
        ["Chrome", CHROME_UA, ""],
        ["Firefox", FIREFOX_UA, ""],
    ])("marks the title on %s", (_name, userAgent, expected) => {
        stubUserAgent(userAgent);

        expect(badge.unreadTitlePrefix(true)).toBe(expected);
    });

    it("never marks the title while the badge is down", () => {
        stubUserAgent(SAFARI_UA);

        expect(badge.unreadTitlePrefix(false)).toBe("");
    });

    describe("trackUnreadTotal", () => {
        it("captures the total and clears while the tab is visible", () => {
            expect(badge.trackUnreadTotal(undefined, 5, false)).toEqual({
                baseline: 5,
                badge: false,
            });
            expect(badge.trackUnreadTotal(2, 5, false)).toEqual({
                baseline: 5,
                badge: false,
            });
        });

        it("reads a first total on a hidden tab as a starting point, not an arrival", () => {
            expect(badge.trackUnreadTotal(undefined, 5, true)).toEqual({
                baseline: 5,
                badge: false,
            });
        });

        it("raises on a rise above the baseline while hidden", () => {
            expect(badge.trackUnreadTotal(5, 6, true)).toEqual({
                baseline: 5,
                badge: true,
            });
        });

        it("leaves the badge alone while hidden without a rise", () => {
            expect(badge.trackUnreadTotal(5, 5, true)).toEqual({ baseline: 5 });
        });

        it("follows decreases while hidden so mail read elsewhere doesn't absorb the next arrival", () => {
            // Away from the tab, the 5 unread get read on the phone…
            const afterReads = badge.trackUnreadTotal(5, 0, true);
            expect(afterReads).toEqual({ baseline: 0 });
            // …then one new mail arrives: it must still raise the badge.
            expect(badge.trackUnreadTotal(afterReads.baseline, 1, true)).toEqual({
                baseline: 0,
                badge: true,
            });
        });
    });
});

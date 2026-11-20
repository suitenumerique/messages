import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SILENT_LOGIN_RETRY_INTERVAL, SILENT_LOGIN_RETRY_KEY } from "../config/constants";
import { canAttemptSilentLogin } from "./silent-login";

describe("canAttemptSilentLogin", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:00:00Z"));
        localStorage.clear();
    });

    afterEach(() => {
        vi.useRealTimers();
        localStorage.clear();
    });

    it("allows a first attempt", () => {
        expect(canAttemptSilentLogin()).toBe(true);
    });

    it("waits for the retry time", () => {
        localStorage.setItem(SILENT_LOGIN_RETRY_KEY, String(Date.now() + 10_000));
        expect(canAttemptSilentLogin()).toBe(false);
        localStorage.setItem(SILENT_LOGIN_RETRY_KEY, String(Date.now() - 1));
        expect(canAttemptSilentLogin()).toBe(true);
    });

    it.each(["garbage", "", "Infinity"])("allows an attempt when the stored time is unreadable (%s)", (value) => {
        localStorage.setItem(SILENT_LOGIN_RETRY_KEY, value);
        expect(canAttemptSilentLogin()).toBe(true);
    });

    it("allows an attempt when the stored time is beyond one interval (clock set back)", () => {
        localStorage.setItem(
            SILENT_LOGIN_RETRY_KEY,
            String(Date.now() + SILENT_LOGIN_RETRY_INTERVAL + 1),
        );
        expect(canAttemptSilentLogin()).toBe(true);
    });
});

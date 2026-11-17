import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../providers/mailbox", () => ({ useMailboxContext: () => ({}) }));
vi.mock("./use-flag", () => ({ default: () => ({}) }));

import { readAtBefore, readAtNow } from "./use-read";

describe("readAtNow", () => {
    afterEach(() => {
        vi.useRealTimers();
    });

    it("is the device time when it is after every message", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:00:00Z"));
        expect(readAtNow(["2026-11-17T11:00:00Z", null, undefined])).toBe("2026-11-17T12:00:00.000Z");
        expect(readAtNow([])).toBe("2026-11-17T12:00:00.000Z");
    });

    it("is the newest message when the device clock is behind", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:00:00Z"));
        expect(readAtNow(["2026-11-17T11:00:00Z", "2026-11-17T12:05:00.123Z"])).toBe(
            "2026-11-17T12:05:00.123Z",
        );
    });

    it("keeps the microseconds of the newest message", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:05:00.123Z"));
        // Same millisecond as the device clock: the message must still be read.
        expect(readAtNow(["2026-11-17T12:05:00.123456Z"])).toBe("2026-11-17T12:05:00.123456Z");
        expect(readAtNow(["2026-11-17T12:10:00.000001Z"])).toBe("2026-11-17T12:10:00.000001Z");
    });

    it("ignores dates it cannot read", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:00:00Z"));
        expect(readAtNow(["garbage"])).toBe("2026-11-17T12:00:00.000Z");
    });
});

describe("readAtBefore", () => {
    it("is 1 ms before the message", () => {
        expect(readAtBefore("2026-11-09T18:30:11Z")).toBe("2026-11-09T18:30:10.999Z");
        expect(readAtBefore("0001-01-01T00:00:00.001Z")).toBe("0001-01-01T00:00:00.000Z");
    });

    it("is null for the first instant the server can store", () => {
        expect(readAtBefore("0001-01-01T00:00:00Z")).toBeNull();
    });
});

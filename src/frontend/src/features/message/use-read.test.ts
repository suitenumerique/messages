import { afterEach, describe, expect, it, vi } from "vitest";

type FlagOptions = { onSuccess: (data: { read_at: string | null; thread_ids: string[]; mailbox_id: string }) => void };
type Patch<T> = (ids: string[] | string, update: (item: T) => T) => void;

const flagOptions: { current?: FlagOptions } = {};
const pinThreads = vi.fn<Patch<Record<string, unknown>>>();
const patchMessages = vi.fn<Patch<Record<string, unknown>>>();

vi.mock("../providers/mailbox", () => ({
    useMailboxContext: () => ({
        selectedMailbox: { id: "mailbox-1" },
        pinThreads,
        patchMessages,
        invalidateThreadsStats: () => {},
    }),
}));
vi.mock("./use-flag", () => ({
    default: (_flag: string, options: FlagOptions) => {
        flagOptions.current = options;
        return {};
    },
}));

import useRead, { readAtBefore, readAtNow } from "./use-read";

describe("useRead cache patch", () => {
    it("derives the read state below the millisecond, as the backend does", () => {
        useRead();
        // The message at .123456 is after the pointer at .123455.
        flagOptions.current!.onSuccess({
            read_at: "2026-11-17T12:00:00.123455Z",
            thread_ids: ["thread-1"],
            mailbox_id: "mailbox-1",
        });
        const createdAt = "2026-11-17T12:00:00.123456Z";
        const thread = pinThreads.mock.calls[0][1]({ messaged_at: createdAt, accesses: [] });
        expect(thread.has_unread).toBe(true);
        const message = patchMessages.mock.calls[0][1]({ created_at: createdAt });
        expect(message.is_unread).toBe(true);
    });
});

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

    it("keeps the microseconds of a message after a device clock behind", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:00:00Z"));
        expect(readAtNow(["2026-11-17T12:05:00.123456Z"])).toBe("2026-11-17T12:05:00.123456Z");
    });

    it("orders messages of the same millisecond by their microseconds", () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-11-17T12:00:00Z"));
        const messages = ["2026-11-17T12:05:00.123999Z", "2026-11-17T12:05:00.123456Z"];
        expect(readAtNow(messages)).toBe("2026-11-17T12:05:00.123999Z");
        expect(readAtNow([...messages].reverse())).toBe("2026-11-17T12:05:00.123999Z");
        // Fewer or no sub-millisecond digits.
        expect(readAtNow(["2026-11-17T12:05:00.1235Z", "2026-11-17T12:05:00.123Z"])).toBe(
            "2026-11-17T12:05:00.1235Z",
        );
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

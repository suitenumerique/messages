import { describe, expect, it } from "vitest";
import type { ThreadsListParams } from "../api/gen";
import {
    isOpenThreadActedUpon,
    THREADS_LIST_NUMERIC_FILTERS,
    THREADS_LIST_STRING_FILTERS,
} from "./mailbox";

describe("ThreadsListParams filter coverage", () => {
    it("classifies every ThreadsListParams key (compile-time check)", () => {
        // Compile-time exhaustiveness guard: if Orval regenerates `ThreadsListParams`
        // with a new key, this type fails to satisfy `true` and `tsc` errors on the
        // instantiation below — forcing the dev to classify the new key in one of
        // the filter constants (or as an explicit param in the threads queryFn).
        type Assert<T extends true> = T;
        type AllThreadsListParamsCovered = Assert<
            Exclude<
                keyof ThreadsListParams,
                | (typeof THREADS_LIST_NUMERIC_FILTERS)[number]
                | (typeof THREADS_LIST_STRING_FILTERS)[number]
                | "mailbox_id"
                | "page"
            > extends never
                ? true
                : false
        >;
        // The real check is the `AllThreadsListParamsCovered` type alias
        // above, evaluated by `tsc --noEmit`. This runtime case keeps the
        // file in the test suite and documents intent.
        const _typeCheck: AllThreadsListParamsCovered = true;
        expect(_typeCheck).toBe(true);
    });
});

describe("isOpenThreadActedUpon", () => {
    it("leaves the open thread when the action targets it", () => {
        expect(isOpenThreadActedUpon("thread-x", "thread-x")).toBe(true);
    });

    it("leaves the open thread when it is among the threads acted upon", () => {
        expect(isOpenThreadActedUpon("thread-x", "thread-x", ["thread-y", "thread-x"])).toBe(true);
    });

    it("keeps the open thread when only other threads are acted upon", () => {
        expect(isOpenThreadActedUpon("thread-x", "thread-x", ["thread-y", "thread-z"])).toBe(false);
    });

    it("keeps the thread the user has opened since the action was fired", () => {
        expect(isOpenThreadActedUpon("thread-x", "thread-y")).toBe(false);
    });

    it("does not take a thread id that merely contains the fired one for it", () => {
        expect(isOpenThreadActedUpon("thread-x", "thread-x2")).toBe(false);
    });

    it("does nothing when no thread was open", () => {
        expect(isOpenThreadActedUpon(undefined, undefined, ["thread-x"])).toBe(false);
    });
});

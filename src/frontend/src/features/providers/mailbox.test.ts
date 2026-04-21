import { describe, expect, it } from "vitest";
import type { ThreadsListParams } from "../api/gen";
import {
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

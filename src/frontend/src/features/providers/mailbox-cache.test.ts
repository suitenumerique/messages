import { describe, expect, it } from "vitest";
import type { InfiniteData } from "@tanstack/react-query";
import type { Message, Thread } from "../api/gen";
import type { messagesListResponse200 } from "../api/gen/messages/messages";
import type { threadsListResponse } from "../api/gen/threads/threads";
import {
    type MessageQueryInvalidationSource,
    applyMessageUpdate,
    mergeOptimisticThreads,
    trimTrailingEmptyPages,
} from "./mailbox-cache";

const makeThread = (id: string, overrides: Partial<Thread> = {}): Thread =>
    ({ id, ...overrides }) as unknown as Thread;

const makeMessage = (
    id: string,
    createdAt: string,
    overrides: Partial<Message> = {},
): Message =>
    ({
        id,
        created_at: createdAt,
        is_unread: false,
        is_trashed: false,
        is_archived: false,
        thread_id: 't1',
        ...overrides,
    }) as unknown as Message;

const makePage = (threads: Thread[], count?: number): threadsListResponse => ({
    data: {
        results: threads,
        count: count ?? threads.length,
        next: null,
        previous: null,
    },
    status: 200,
    headers: new Headers(),
});

const makeInfinite = (
    pages: threadsListResponse[],
): InfiniteData<threadsListResponse> => ({
    pages,
    pageParams: pages.map((_, i) => i + 1),
});

const flatten = (data: InfiniteData<threadsListResponse>): string[] =>
    data.pages.flatMap(p => p.data.results.map(t => t.id));

describe("mergeOptimisticThreads", () => {
    it("returns newData untouched when no optimistic IDs are tracked", () => {
        const oldData = makeInfinite([makePage([makeThread('A'), makeThread('B')])]);
        const newData = makeInfinite([makePage([makeThread('A'), makeThread('B')])]);
        const ids = new Set<string>();

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(result).toBe(newData);
        expect(ids.size).toBe(0);
    });

    it("returns newData untouched when oldData is undefined", () => {
        const newData = makeInfinite([makePage([makeThread('A')])]);
        const ids = new Set(['A']);

        const result = mergeOptimisticThreads(undefined, newData, ids);

        expect(result).toBe(newData);
    });

    it("drops an optimistic ID when a real refetch shrinks the result set while still returning that thread", () => {
        // Only path where we can tell for sure the server confirmed the thread:
        // the returned ID set genuinely differs from the local snapshot (other
        // threads disappeared). A pure same-set response is ambiguous (could be
        // a local setQueryData patch) and is treated conservatively elsewhere.
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
        ]);
        const newData = makeInfinite([makePage([makeThread('A'), makeThread('B')])]);
        const ids = new Set(['A']);

        mergeOptimisticThreads(oldData, newData, ids);

        expect(ids.has('A')).toBe(false);
    });

    it("re-inserts a missing optimistic thread at its original index within page 0", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('B'), makeThread('C'), makeThread('D')]),
        ]);
        const ids = new Set(['A']);

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(flatten(result)).toEqual(['A', 'B', 'C', 'D']);
        expect(ids.has('A')).toBe(true); // still protected, server did not return it
    });

    it("re-inserts a missing optimistic thread inside the page it originally belonged to (not flattened into page 0)", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
            makePage([makeThread('E'), makeThread('F'), makeThread('G'), makeThread('H')]),
        ]);
        // Server filtered out F (optimistically read) from page 1
        const newData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
            makePage([makeThread('E'), makeThread('G'), makeThread('H')]),
        ]);
        const ids = new Set(['F']);

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(result.pages).toHaveLength(2);
        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B', 'C', 'D']);
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['E', 'F', 'G', 'H']);
    });

    it("never produces duplicates across pages when flattened (regression for Bug 2)", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B')]),
            makePage([makeThread('C'), makeThread('D')]),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('B')]),
            makePage([makeThread('C'), makeThread('D')]),
        ]);
        const ids = new Set(['A']);

        const result = mergeOptimisticThreads(oldData, newData, ids);
        const flat = flatten(result);

        // Each ID appears exactly once after flattening.
        const counts = flat.reduce<Record<string, number>>((acc, id) => {
            acc[id] = (acc[id] ?? 0) + 1;
            return acc;
        }, {});
        expect(counts).toEqual({ A: 1, B: 1, C: 1, D: 1 });
    });

    it("handles multiple missing threads on multiple pages independently", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('E'), makeThread('F')]),
        ]);
        // A and E were optimistically read and filtered out
        const newData = makeInfinite([
            makePage([makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('F')]),
        ]);
        const ids = new Set(['A', 'E']);

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B', 'C']);
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['D', 'E', 'F']);
    });

    it("keeps the original order when two optimistic threads from the same page are both missing", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
        ]);
        const newData = makeInfinite([makePage([makeThread('C'), makeThread('D')])]);
        const ids = new Set(['A', 'B']);

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B', 'C', 'D']);
    });

    it("inflates the count on the impacted page only", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B')], 100),
            makePage([makeThread('C'), makeThread('D')], 100),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('B')], 99),
            makePage([makeThread('C'), makeThread('D')], 99),
        ]);
        const ids = new Set(['A']);

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(result.pages[0].data.count).toBe(100); // 99 + 1 re-inserted
        expect(result.pages[1].data.count).toBe(99); // untouched
    });

    it("drops optimistic IDs that the server confirmed while keeping missing ones protected", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
        ]);
        // A still returned, B filtered out
        const newData = makeInfinite([makePage([makeThread('A'), makeThread('C')])]);
        const ids = new Set(['A', 'B']);

        mergeOptimisticThreads(oldData, newData, ids);

        expect(ids.has('A')).toBe(false);
        expect(ids.has('B')).toBe(true);
    });

    it("keeps optimistic IDs protected during fetchNextPage (existing pages come from local cache, not the server)", () => {
        // Scenario: user patched A and B optimistically on page 0, then scrolled,
        // triggering fetchNextPage. The new data concatenates the cached page 0
        // (still carrying A and B) with a freshly-fetched page 1. We must NOT
        // interpret "A and B are in newThreadIds" as server confirmation.
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('E')]),
        ]);
        const ids = new Set(['A', 'B']);

        mergeOptimisticThreads(oldData, newData, ids);

        expect(ids.has('A')).toBe(true);
        expect(ids.has('B')).toBe(true);
    });

    it("keeps optimistic IDs protected when structuralSharing runs on a local setQueryData patch (same thread IDs, different properties)", () => {
        // Scenario: user marked A as read (A added to optimisticIds). User opens
        // B. The onSuccess of B's mutation calls `_updateThreadAccessReadAt`
        // which does `setQueriesData` — React Query invokes structuralSharing
        // on every cache write. oldData and newData carry the SAME thread IDs
        // (only B's `has_unread` flipped), so finding A in newThreadIds is not
        // a server confirmation and must not strip A from the optimistic set.
        const oldData = makeInfinite([
            makePage([
                makeThread('A', { has_unread: false } as Partial<Thread>),
                makeThread('B', { has_unread: true } as Partial<Thread>),
                makeThread('C', { has_unread: true } as Partial<Thread>),
            ]),
        ]);
        const newData = makeInfinite([
            makePage([
                makeThread('A', { has_unread: false } as Partial<Thread>),
                makeThread('B', { has_unread: false } as Partial<Thread>), // patched
                makeThread('C', { has_unread: true } as Partial<Thread>),
            ]),
        ]);
        const ids = new Set(['A']);

        mergeOptimisticThreads(oldData, newData, ids);

        expect(ids.has('A')).toBe(true);
    });

    it("reconstructs the full mark-then-mark scenario: A still in optimisticIds after B is patched", () => {
        // Two successive reads (A then B). The second patch (B) must not
        // silently drop A from the optimistic set.
        const afterAPatched = makeInfinite([
            makePage([
                makeThread('A', { has_unread: false } as Partial<Thread>),
                makeThread('B', { has_unread: true } as Partial<Thread>),
            ]),
        ]);
        const afterBPatched = makeInfinite([
            makePage([
                makeThread('A', { has_unread: false } as Partial<Thread>),
                makeThread('B', { has_unread: false } as Partial<Thread>),
            ]),
        ]);
        const ids = new Set(['A']); // A was registered after its own patch

        // structuralSharing fires because setQueryData patches B.
        mergeOptimisticThreads(afterAPatched, afterBPatched, ids);

        expect(ids.has('A')).toBe(true);
    });

    it("still re-injects missing optimistics after a post-fetchNextPage refetch (regression for disappearing threads)", () => {
        // Full chain: user marks A and B as read on page 0, scrolls to load
        // page 1, then a polling-triggered refetch fires. The server (filter
        // "unread") drops A and B. Both should be re-injected at their original
        // index in page 0.
        const oldDataAfterNextPage = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('E')]),
        ]);
        const refetchedData = makeInfinite([
            makePage([makeThread('C')]),
            makePage([makeThread('D'), makeThread('E')]),
        ]);
        const ids = new Set(['A', 'B']);

        const result = mergeOptimisticThreads(oldDataAfterNextPage, refetchedData, ids);

        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B', 'C']);
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['D', 'E']);
        // Still protected — server never reconfirmed them.
        expect(ids.has('A')).toBe(true);
        expect(ids.has('B')).toBe(true);
    });

    it("does not mutate newData when there are no missing optimistics on a page", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B')]),
            makePage([makeThread('C'), makeThread('D')]),
        ]);
        // Only page 1 has a missing optimistic
        const newData = makeInfinite([
            makePage([makeThread('A'), makeThread('B')]),
            makePage([makeThread('D')]),
        ]);
        const newPage0 = newData.pages[0];
        const ids = new Set(['C']);

        const result = mergeOptimisticThreads(oldData, newData, ids);

        expect(result.pages[0]).toBe(newPage0); // page 0 reference preserved — no unnecessary copy
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['C', 'D']);
    });
});

describe("trimTrailingEmptyPages", () => {
    it("returns the data untouched when no trailing page is empty", () => {
        const data = makeInfinite([makePage([makeThread('A')])]);

        const result = trimTrailingEmptyPages(data);

        expect(result).toBe(data);
    });

    it("removes a trailing empty page (e.g. after a bulk trash shrinks the list)", () => {
        const data = makeInfinite([
            makePage([makeThread('A'), makeThread('B')]),
            makePage([]),
        ]);

        const result = trimTrailingEmptyPages(data);

        expect(result.pages).toHaveLength(1);
        expect(result.pageParams).toHaveLength(1);
        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B']);
    });

    it("removes multiple trailing empty pages in one pass", () => {
        const data = makeInfinite([
            makePage([makeThread('A')]),
            makePage([]),
            makePage([]),
        ]);

        const result = trimTrailingEmptyPages(data);

        expect(result.pages).toHaveLength(1);
    });

    it("keeps empty pages that are not at the tail", () => {
        // An empty page sandwiched between non-empty ones would be a server
        // bug anyway, but the trim must not touch it (removing it would
        // reshuffle page indices that `mergeOptimisticThreads` keys off).
        const data = makeInfinite([
            makePage([makeThread('A')]),
            makePage([]),
            makePage([makeThread('B')]),
        ]);

        const result = trimTrailingEmptyPages(data);

        expect(result).toBe(data);
    });

    it("always keeps at least one page even when every page is empty", () => {
        const data = makeInfinite([makePage([]), makePage([])]);

        const result = trimTrailingEmptyPages(data);

        expect(result.pages).toHaveLength(1);
        expect(result.pageParams).toHaveLength(1);
    });
});

describe("applyMessageUpdate", () => {
    const buildCache = (messages: Message[]): messagesListResponse200 => ({
        data: messages,
        status: 200,
    });

    it("returns oldData untouched when it is undefined or missing data", () => {
        expect(applyMessageUpdate(undefined, 't1', { type: 'update', metadata: {} })).toBeUndefined();
    });

    describe("delete", () => {
        it("removes messages whose id is targeted", () => {
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z'),
                makeMessage('m2', '2026-01-02T00:00:00Z'),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'delete',
                metadata: { ids: ['m1'] },
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.map(m => m.id)).toEqual(['m2']);
        });

        it("keeps all messages when the thread itself is the delete target (no id filter)", () => {
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z'),
                makeMessage('m2', '2026-01-02T00:00:00Z'),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'delete',
                metadata: { threadIds: ['t1'] },
            };

            const result = applyMessageUpdate(cache, 't1', source);

            // Thread-level delete drops the whole thread upstream; per-message cache
            // keeps the rows untouched so consumers reading another thread are unaffected.
            expect(result?.data.map(m => m.id)).toEqual(['m1', 'm2']);
        });
    });

    describe("update with read pointer", () => {
        it("marks every message as unread when readAt is null", () => {
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: false }),
                makeMessage('m2', '2026-01-02T00:00:00Z', { is_unread: false }),
                makeMessage('m3', '2026-01-03T00:00:00Z', { is_unread: false }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { threadIds: ['t1'] },
                payload: { is_unread: true },
                readAt: null,
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.every(m => m.is_unread)).toBe(true);
        });

        it("marks every message as read when readAt is now()", () => {
            const now = '2026-12-31T23:59:59Z';
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: true }),
                makeMessage('m2', '2026-01-02T00:00:00Z', { is_unread: true }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { threadIds: ['t1'] },
                payload: { is_unread: false },
                readAt: now,
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.every(m => !m.is_unread)).toBe(true);
        });

        it("derives is_unread from the pointer for 'mark unread from here' (regression for Bug 1)", () => {
            // Scenario: thread with [M1, M2, M3] all read. User clicks 'Mark as unread
            // from M2'. readAt = M2.created_at - 1ms. Expected: M1 stays read,
            // M2 and M3 become unread.
            const m2CreatedAt = '2026-01-02T00:00:00.000Z';
            const readAt = '2026-01-01T23:59:59.999Z'; // M2 - 1ms
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: false }),
                makeMessage('m2', m2CreatedAt, { is_unread: false }),
                makeMessage('m3', '2026-01-03T00:00:00Z', { is_unread: false }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { threadIds: ['t1'] },
                // use-read sends payload is_unread=false for any non-null readAt —
                // the fix must NOT blindly apply this payload to messages after the pointer.
                payload: { is_unread: false },
                readAt,
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.map(m => ({ id: m.id, is_unread: m.is_unread }))).toEqual([
                { id: 'm1', is_unread: false },
                { id: 'm2', is_unread: true },
                { id: 'm3', is_unread: true },
            ]);
        });

        it("derives is_unread for 'mark read up to here'", () => {
            // Scenario: readAt = M2.created_at. M1 and M2 become read, M3 stays unread.
            const m2CreatedAt = '2026-01-02T00:00:00Z';
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: true }),
                makeMessage('m2', m2CreatedAt, { is_unread: true }),
                makeMessage('m3', '2026-01-03T00:00:00Z', { is_unread: true }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { threadIds: ['t1'] },
                payload: { is_unread: false },
                readAt: m2CreatedAt,
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.map(m => ({ id: m.id, is_unread: m.is_unread }))).toEqual([
                { id: 'm1', is_unread: false },
                { id: 'm2', is_unread: false },
                { id: 'm3', is_unread: true },
            ]);
        });

        it("does not touch messages whose thread is not targeted", () => {
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { thread_id: 't1', is_unread: true }),
                makeMessage('m2', '2026-01-02T00:00:00Z', { thread_id: 't1', is_unread: true }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { threadIds: ['other-thread'] },
                payload: { is_unread: false },
                readAt: null,
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.every(m => m.is_unread)).toBe(true);
        });
    });

    describe("update without read pointer", () => {
        it("applies payload to targeted messages (e.g. is_trashed toggle)", () => {
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { is_trashed: false }),
                makeMessage('m2', '2026-01-02T00:00:00Z', { is_trashed: false }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { ids: ['m1'] },
                payload: { is_trashed: true },
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data.find(m => m.id === 'm1')?.is_trashed).toBe(true);
            expect(result?.data.find(m => m.id === 'm2')?.is_trashed).toBe(false);
        });

        it("leaves is_unread untouched when no read pointer is provided", () => {
            const cache = buildCache([
                makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: true }),
            ]);
            const source: MessageQueryInvalidationSource = {
                type: 'update',
                metadata: { threadIds: ['t1'] },
                payload: { is_archived: true },
            };

            const result = applyMessageUpdate(cache, 't1', source);

            expect(result?.data[0].is_unread).toBe(true);
            expect(result?.data[0].is_archived).toBe(true);
        });
    });
});

import { describe, expect, it } from "vitest";
import { QueryClient, type InfiniteData } from "@tanstack/react-query";
import type { Message, Thread } from "../api/gen";
import type { messagesListResponse200 } from "../api/gen/messages/messages";
import type { threadsListResponse } from "../api/gen/threads/threads";
import {
    getMailboxThreadsListQueryKeyPrefix,
    mergePinnedThreads,
    patchMessagesInCache,
    patchThreadsInCache,
    removeMessagesFromCache,
    trimTrailingEmptyPages,
} from "./mailbox-cache";

// Test-only relaxation of the Thread / Message types: tests construct minimal
// shapes carrying only the fields they assert on. Using `as unknown as` once
// here avoids polluting every fixture with type assertions.
type MockThread = Pick<Thread, 'id'> & Partial<Thread>;
type MockMessage = Pick<Message, 'id' | 'created_at'> & Partial<Message>;

const makeThread = (id: string, overrides: Partial<Thread> = {}): Thread =>
    ({ id, ...overrides } as MockThread) as unknown as Thread;

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
    } as MockMessage) as unknown as Message;

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

describe("mergePinnedThreads", () => {
    it("returns newData untouched when no pinned IDs are tracked", () => {
        const oldData = makeInfinite([makePage([makeThread('A'), makeThread('B')])]);
        const newData = makeInfinite([makePage([makeThread('A'), makeThread('B')])]);
        const ids = new Set<string>();

        const result = mergePinnedThreads(oldData, newData, ids);

        expect(result).toBe(newData);
        expect(ids.size).toBe(0);
    });

    it("returns newData untouched when oldData is undefined", () => {
        const newData = makeInfinite([makePage([makeThread('A')])]);
        const ids = new Set(['A']);

        const result = mergePinnedThreads(undefined, newData, ids);

        expect(result).toBe(newData);
    });

    it("re-inserts a missing pinned thread at its original index within page 0", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('B'), makeThread('C'), makeThread('D')]),
        ]);
        const ids = new Set(['A']);

        const result = mergePinnedThreads(oldData, newData, ids);

        expect(flatten(result)).toEqual(['A', 'B', 'C', 'D']);
        expect(ids.has('A')).toBe(true); // still protected, server did not return it
    });

    it("re-inserts a missing pinned thread inside the page it originally belonged to (not flattened into page 0)", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
            makePage([makeThread('E'), makeThread('F'), makeThread('G'), makeThread('H')]),
        ]);
        // Server filtered out F (pinned after a read) from page 1
        const newData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
            makePage([makeThread('E'), makeThread('G'), makeThread('H')]),
        ]);
        const ids = new Set(['F']);

        const result = mergePinnedThreads(oldData, newData, ids);

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

        const result = mergePinnedThreads(oldData, newData, ids);
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
        // A and E were pinned and filtered out
        const newData = makeInfinite([
            makePage([makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('F')]),
        ]);
        const ids = new Set(['A', 'E']);

        const result = mergePinnedThreads(oldData, newData, ids);

        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B', 'C']);
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['D', 'E', 'F']);
    });

    it("keeps the original order when two pinned threads from the same page are both missing", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C'), makeThread('D')]),
        ]);
        const newData = makeInfinite([makePage([makeThread('C'), makeThread('D')])]);
        const ids = new Set(['A', 'B']);

        const result = mergePinnedThreads(oldData, newData, ids);

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

        const result = mergePinnedThreads(oldData, newData, ids);

        expect(result.pages[0].data.count).toBe(100); // 99 + 1 re-inserted
        expect(result.pages[1].data.count).toBe(99); // untouched
    });

    it("does not duplicate a pinned thread the server moved to another page", () => {
        // Scenario: A is pinned and lived on page 0. A refetch returns A on
        // page 1 instead (server-side reordering, e.g. a new unread thread
        // bumped down older ones). `mergePinnedThreads` must NOT also re-
        // inject A on page 0, otherwise flattening would yield two A's.
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('E')]),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('B'), makeThread('C'), makeThread('D')]),
            makePage([makeThread('E'), makeThread('A')]),
        ]);
        const ids = new Set(['A']);

        const result = mergePinnedThreads(oldData, newData, ids);
        const flat = flatten(result);

        expect(flat).toEqual(['B', 'C', 'D', 'E', 'A']);
        expect(flat.filter(id => id === 'A')).toHaveLength(1);
    });

    it("still re-injects missing pinned threads after a post-fetchNextPage refetch (regression for disappearing threads)", () => {
        // Full chain: user pins A and B on page 0, scrolls to load page 1,
        // then a polling-triggered refetch fires. The server (filter "unread")
        // drops A and B. Both should be re-injected at their original index
        // in page 0.
        const oldDataAfterNextPage = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
            makePage([makeThread('D'), makeThread('E')]),
        ]);
        const refetchedData = makeInfinite([
            makePage([makeThread('C')]),
            makePage([makeThread('D'), makeThread('E')]),
        ]);
        const ids = new Set(['A', 'B']);

        const result = mergePinnedThreads(oldDataAfterNextPage, refetchedData, ids);

        expect(result.pages[0].data.results.map(t => t.id)).toEqual(['A', 'B', 'C']);
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['D', 'E']);
        // Still protected — server never reconfirmed them.
        expect(ids.has('A')).toBe(true);
        expect(ids.has('B')).toBe(true);
    });

    it("does not mutate newData when there are no missing pinned threads on a page", () => {
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B')]),
            makePage([makeThread('C'), makeThread('D')]),
        ]);
        // Only page 1 has a missing pinned thread
        const newData = makeInfinite([
            makePage([makeThread('A'), makeThread('B')]),
            makePage([makeThread('D')]),
        ]);
        const newPage0 = newData.pages[0];
        const ids = new Set(['C']);

        const result = mergePinnedThreads(oldData, newData, ids);

        expect(result.pages[0]).toBe(newPage0); // page 0 reference preserved — no unnecessary copy
        expect(result.pages[1].data.results.map(t => t.id)).toEqual(['C', 'D']);
    });

    it("returns newData by reference when no page needs re-injection (preserves InfiniteData identity for downstream selectors)", () => {
        // Pinned thread 'C' is still present in newData → nothing to re-inject.
        // The function must short-circuit and return newData itself so that
        // React Query's structuralSharing path does not trigger spurious
        // re-renders for unrelated cache writes (e.g. fetchNextPage, local
        // patches that hit this same callback).
        const oldData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
        ]);
        const newData = makeInfinite([
            makePage([makeThread('A'), makeThread('B'), makeThread('C')]),
        ]);
        const ids = new Set(['C']);

        const result = mergePinnedThreads(oldData, newData, ids);

        expect(result).toBe(newData);
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
        // reshuffle page indices that `mergePinnedThreads` keys off).
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

describe("patchThreadsInCache", () => {
    const MAILBOX_ID = 'mb-1';
    const listKey = [...getMailboxThreadsListQueryKeyPrefix(MAILBOX_ID), 'list', ''];

    it("applies the patcher only to threads whose id is in the target list", () => {
        const qc = new QueryClient();
        qc.setQueryData(listKey, makeInfinite([makePage([
            makeThread('A', { has_unread: true }),
            makeThread('B', { has_unread: true }),
            makeThread('C', { has_unread: true }),
        ])]));

        patchThreadsInCache(qc, MAILBOX_ID, ['A', 'C'], (thread) => ({
            ...thread,
            has_unread: false,
        }));

        const cached = qc.getQueryData<InfiniteData<threadsListResponse>>(listKey);
        const results = cached!.pages[0].data.results;
        expect(results.find(t => t.id === 'A')!.has_unread).toBe(false);
        expect(results.find(t => t.id === 'B')!.has_unread).toBe(true);
        expect(results.find(t => t.id === 'C')!.has_unread).toBe(false);
    });

    it("propagates the patch across every cached list variant of the mailbox (filters, search…)", () => {
        const qc = new QueryClient();
        const unreadKey = [...getMailboxThreadsListQueryKeyPrefix(MAILBOX_ID), 'list', 'has_unread=1'];

        qc.setQueryData(listKey, makeInfinite([makePage([makeThread('A', { has_starred: false })])]));
        qc.setQueryData(unreadKey, makeInfinite([makePage([makeThread('A', { has_starred: false })])]));

        patchThreadsInCache(qc, MAILBOX_ID, ['A'], (t) => ({ ...t, has_starred: true }));

        const fromList = qc.getQueryData<InfiniteData<threadsListResponse>>(listKey);
        const fromUnread = qc.getQueryData<InfiniteData<threadsListResponse>>(unreadKey);
        expect(fromList!.pages[0].data.results[0].has_starred).toBe(true);
        expect(fromUnread!.pages[0].data.results[0].has_starred).toBe(true);
    });

    it("is a no-op when threadIds is empty", () => {
        const qc = new QueryClient();
        const initial = makeInfinite([makePage([makeThread('A')])]);
        qc.setQueryData(listKey, initial);

        patchThreadsInCache(qc, MAILBOX_ID, [], (t) => ({ ...t, has_unread: false }));

        const after = qc.getQueryData<InfiniteData<threadsListResponse>>(listKey);
        // Reference identity preserved — no re-render churn for an empty patch.
        expect(after).toBe(initial);
    });

    it("does not touch other mailboxes' caches", () => {
        const qc = new QueryClient();
        const otherKey = [...getMailboxThreadsListQueryKeyPrefix('mb-other'), 'list', ''];
        qc.setQueryData(listKey, makeInfinite([makePage([makeThread('A', { has_unread: true })])]));
        qc.setQueryData(otherKey, makeInfinite([makePage([makeThread('A', { has_unread: true })])]));

        patchThreadsInCache(qc, MAILBOX_ID, ['A'], (t) => ({ ...t, has_unread: false }));

        const otherMailbox = qc.getQueryData<InfiniteData<threadsListResponse>>(otherKey);
        expect(otherMailbox!.pages[0].data.results[0].has_unread).toBe(true);
    });

    it("preserves the reference of pages that contain none of the targeted thread ids", () => {
        // Without per-page short-circuit, the threads-list query's custom
        // structuralSharing (no default deep ref-preserving diff) would let
        // these unnecessary copies propagate to selectors and trigger
        // re-renders for unrelated pages. Page 1 here is untouched and must
        // keep its identity.
        const qc = new QueryClient();
        const initial = makeInfinite([
            makePage([makeThread('A', { has_unread: true })]),
            makePage([makeThread('B', { has_unread: true })]),
        ]);
        qc.setQueryData(listKey, initial);
        const untouchedPage = initial.pages[1];

        patchThreadsInCache(qc, MAILBOX_ID, ['A'], (t) => ({ ...t, has_unread: false }));

        const after = qc.getQueryData<InfiniteData<threadsListResponse>>(listKey)!;
        expect(after.pages[1]).toBe(untouchedPage);
    });

    it("returns the cached data by reference when no page contains a targeted thread", () => {
        // Patch targets a thread id absent from the cache. The whole
        // InfiniteData wrapper must keep its identity so selectors do not
        // observe a phantom change.
        const qc = new QueryClient();
        const initial = makeInfinite([makePage([makeThread('A', { has_unread: true })])]);
        qc.setQueryData(listKey, initial);

        patchThreadsInCache(qc, MAILBOX_ID, ['Z'], (t) => ({ ...t, has_unread: false }));

        const after = qc.getQueryData<InfiniteData<threadsListResponse>>(listKey);
        expect(after).toBe(initial);
    });
});

describe("patchMessagesInCache", () => {
    const buildCache = (messages: Message[]): messagesListResponse200 => ({
        data: messages,
        status: 200,
    });

    it("applies the patcher to every message of the targeted thread", () => {
        const qc = new QueryClient();
        qc.setQueryData(['messages', 't1'], buildCache([
            makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: false }),
            makeMessage('m2', '2026-01-02T00:00:00Z', { is_unread: false }),
        ]));

        patchMessagesInCache(qc, 't1', (m) => ({ ...m, is_unread: true }));

        const cached = qc.getQueryData<messagesListResponse200>(['messages', 't1']);
        expect(cached!.data.every(m => m.is_unread)).toBe(true);
    });

    it("is a no-op when the thread has no cached messages", () => {
        const qc = new QueryClient();

        patchMessagesInCache(qc, 't1', (m) => ({ ...m, is_unread: true }));

        expect(qc.getQueryData(['messages', 't1'])).toBeUndefined();
    });

    it("does not touch the cache of another thread", () => {
        const qc = new QueryClient();
        qc.setQueryData(['messages', 't1'], buildCache([
            makeMessage('m1', '2026-01-01T00:00:00Z', { is_unread: false }),
        ]));
        qc.setQueryData(['messages', 't2'], buildCache([
            makeMessage('m2', '2026-01-02T00:00:00Z', { is_unread: false }),
        ]));

        patchMessagesInCache(qc, 't1', (m) => ({ ...m, is_unread: true }));

        const t2 = qc.getQueryData<messagesListResponse200>(['messages', 't2']);
        expect(t2!.data[0].is_unread).toBe(false);
    });
});

describe("removeMessagesFromCache", () => {
    const buildCache = (messages: Message[]): messagesListResponse200 => ({
        data: messages,
        status: 200,
    });

    it("drops the messages whose ids are listed", () => {
        const qc = new QueryClient();
        qc.setQueryData(['messages', 't1'], buildCache([
            makeMessage('m1', '2026-01-01T00:00:00Z'),
            makeMessage('m2', '2026-01-02T00:00:00Z'),
            makeMessage('m3', '2026-01-03T00:00:00Z'),
        ]));

        removeMessagesFromCache(qc, 't1', ['m1', 'm3']);

        const cached = qc.getQueryData<messagesListResponse200>(['messages', 't1']);
        expect(cached!.data.map(m => m.id)).toEqual(['m2']);
    });

    it("is a no-op when messageIds is empty", () => {
        const qc = new QueryClient();
        const initial = buildCache([makeMessage('m1', '2026-01-01T00:00:00Z')]);
        qc.setQueryData(['messages', 't1'], initial);

        removeMessagesFromCache(qc, 't1', []);

        const after = qc.getQueryData<messagesListResponse200>(['messages', 't1']);
        expect(after).toBe(initial);
    });

    it("is a no-op when the thread has no cached messages", () => {
        const qc = new QueryClient();

        removeMessagesFromCache(qc, 't1', ['m1']);

        expect(qc.getQueryData(['messages', 't1'])).toBeUndefined();
    });
});

import type { InfiniteData, QueryClient } from "@tanstack/react-query";
import type { Message, Thread } from "../api/gen";
import type { messagesListResponse200 } from "../api/gen/messages/messages";
import type { threadsListResponse } from "../api/gen/threads/threads";

export type ThreadPatcher = (thread: Thread) => Thread;
export type MessagePatcher = (message: Message) => Message;

/**
 * Merge-back pinned threads that the server filtered out of a fresh response.
 *
 * Why this exists: a mutation may push a thread out of the active filter
 * (e.g. mark-as-read while viewing "unread"). We patch the local cache and
 * pin the thread so the next server refetch — which no longer returns it —
 * does not make it disappear under the user's cursor.
 *
 * Pin lifecycle: a pin is added by `pinThreads`, dropped explicitly by
 * `unpinThreads` (called by mutations that move the thread out of the view),
 * and the whole set is cleared when the user changes filter or mailbox.
 * When the server returns the thread on its own, the pin becomes inert —
 * `mergePinnedThreads` only re-injects missing threads — so we deliberately
 * do not purge "confirmed" pins here. The server is always authoritative
 * for the threads it returns; the pin is just a fallback for the ones it
 * filters out.
 *
 * Re-insertion preserves **per-page semantics**: a thread missing from page
 * N is re-inserted in page N at its original index, so downstream flattening
 * (`pages.flatMap(p => p.data.results)`) never yields duplicates.
 */
export const mergePinnedThreads = (
    oldData: InfiniteData<threadsListResponse> | undefined,
    newData: InfiniteData<threadsListResponse>,
    pinnedIds: Set<string>,
): InfiniteData<threadsListResponse> => {
    if (!oldData || pinnedIds.size === 0) return newData;

    const newThreadIds = new Set<string>();
    newData.pages.forEach(page =>
        page.data.results.forEach(t => newThreadIds.add(t.id))
    );

    let mutated = false;
    const mergedPages = newData.pages.map((newPage, pageIdx) => {
        const oldPage = oldData.pages[pageIdx];
        if (!oldPage) return newPage;

        const missing: { index: number; thread: Thread }[] = [];
        oldPage.data.results.forEach((thread, idx) => {
            if (pinnedIds.has(thread.id) && !newThreadIds.has(thread.id)) {
                missing.push({ index: idx, thread });
            }
        });

        if (missing.length === 0) return newPage;

        const results = [...newPage.data.results];
        // Ascending index order so earlier splices do not shift later indices.
        missing.sort((a, b) => a.index - b.index);
        for (const { index, thread } of missing) {
            results.splice(Math.min(index, results.length), 0, thread);
        }

        mutated = true;
        return {
            ...newPage,
            data: {
                ...newPage.data,
                count: newPage.data.count + missing.length,
                results,
            },
        };
    });

    return mutated ? { ...newData, pages: mergedPages } : newData;
};

/**
 * Drop trailing empty pages from an infinite query snapshot.
 *
 * Why: after a bulk mutation that shrinks the list (e.g. trash 25 threads
 * when only 40 were loaded across 2 pages), the server no longer has enough
 * data to fill all pages the client already cached. The 404 path is converted
 * by the query layer into an empty terminal page, which we remove here so
 * subsequent refetches stop targeting a non-existent page.
 *
 * Always keeps at least one page to stay compatible with React Query's
 * infinite query invariants.
 */
export const trimTrailingEmptyPages = (
    data: InfiniteData<threadsListResponse>,
): InfiniteData<threadsListResponse> => {
    let keep = data.pages.length;
    while (keep > 1 && data.pages[keep - 1].data.results.length === 0) {
        keep--;
    }
    if (keep === data.pages.length) return data;
    return {
        ...data,
        pages: data.pages.slice(0, keep),
        pageParams: data.pageParams.slice(0, keep),
    };
};

/**
 * Query key prefix shared between threads list query definitions and
 * cross-variant cache patches. Re-exported here so cache helpers do not
 * import from the provider (which would create a circular dependency).
 */
export const getMailboxThreadsListQueryKeyPrefix = (mailboxId: string | undefined) =>
    ['threads', mailboxId];

/**
 * Apply `patcher` to every thread whose id is in `threadIds`, across every
 * cached variant of the mailbox threads list (search, filters, etc.).
 */
export const patchThreadsInCache = (
    queryClient: QueryClient,
    mailboxId: string | undefined,
    threadIds: string[],
    patcher: ThreadPatcher,
): void => {
    if (threadIds.length === 0) return;
    const targets = new Set(threadIds);
    queryClient.setQueriesData<InfiniteData<threadsListResponse>>(
        { queryKey: getMailboxThreadsListQueryKeyPrefix(mailboxId) },
        (oldData) => {
            if (!oldData) return oldData;
            let mutated = false;
            const pages = oldData.pages.map((page) => {
                if (!page.data.results.some((t) => targets.has(t.id))) return page;
                mutated = true;
                return {
                    ...page,
                    data: {
                        ...page.data,
                        results: page.data.results.map((thread) =>
                            targets.has(thread.id) ? patcher(thread) : thread
                        ),
                    },
                };
            });
            return mutated ? { ...oldData, pages } : oldData;
        },
    );
};

/**
 * Apply `patcher` to every message of `threadId` in the per-thread messages
 * cache. No-op when the thread has no cached messages.
 */
export const patchMessagesInCache = (
    queryClient: QueryClient,
    threadId: Thread['id'],
    patcher: MessagePatcher,
): void => {
    queryClient.setQueryData<messagesListResponse200>(
        ['messages', threadId],
        (oldData) => {
            if (!oldData?.data) return oldData;
            return { ...oldData, data: oldData.data.map(patcher) };
        },
    );
};

/**
 * Drop the messages whose ids are listed from the cache of `threadId`.
 */
export const removeMessagesFromCache = (
    queryClient: QueryClient,
    threadId: Thread['id'],
    messageIds: Message['id'][],
): void => {
    if (messageIds.length === 0) return;
    const targets = new Set(messageIds);
    queryClient.setQueryData<messagesListResponse200>(
        ['messages', threadId],
        (oldData) => {
            if (!oldData?.data) return oldData;
            return { ...oldData, data: oldData.data.filter((m) => !targets.has(m.id)) };
        },
    );
};

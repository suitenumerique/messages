import type { InfiniteData } from "@tanstack/react-query";
import type { Message, Thread } from "../api/gen";
import type { messagesListResponse200 } from "../api/gen/messages/messages";
import type { threadsListResponse } from "../api/gen/threads/threads";

export type MessageQueryInvalidationSource = {
    type: 'delete' | 'update';
    metadata: { ids?: Message['id'][], threadIds?: Thread['id'][] };
    payload?: Partial<Message>;
    /** When updating read state, optimistically patch ThreadAccess.read_at in the threads cache. */
    threadAccessReadAt?: { mailboxId: string; readAt: string | null };
    /** Optimistically patch ThreadAccess.starred_at in the threads cache. */
    threadAccessStarredAt?: { mailboxId: string; starredAt: string | null };
    /**
     * When set, drives the per-message `is_unread` flag: messages created
     * at or before this timestamp become read, messages after become unread.
     * `null` is the "mark everything unread" sentinel.
     */
    readAt?: string | null;
    /** When true, skip the threads list refetch (rely on optimistic cache only). */
    skipThreadsRefetch?: boolean;
};

/**
 * Merge-back optimistically patched threads that the server filtered out of a
 * fresh response.
 *
 * Why this exists: the user may mutate a thread in a way that pushes it out
 * of the active filter (e.g. mark-as-read while viewing "unread"). We patch
 * the local cache optimistically and flag the thread in `optimisticIds` so
 * that the next server refetch — which no longer returns the thread — does
 * not make it disappear under the user's cursor.
 *
 * Contract:
 * - Runs on every `threads` refetch (per `structuralSharing` semantics).
 * - Mutates `optimisticIds`: removes IDs the server returned again (they are
 *   no longer in jeopardy of being filtered out).
 * - Re-inserts missing optimistic threads **in their original page** at the
 *   index they held in `oldData`, preserving per-page semantics so downstream
 *   flattening (`pages.flatMap(p => p.data.results)`) never yields duplicates.
 */
export const mergeOptimisticThreads = (
    oldData: InfiniteData<threadsListResponse> | undefined,
    newData: InfiniteData<threadsListResponse>,
    optimisticIds: Set<string>,
): InfiniteData<threadsListResponse> => {
    if (!oldData || optimisticIds.size === 0) return newData;

    const newThreadIds = new Set<string>();
    newData.pages.forEach(page =>
        page.data.results.forEach(t => newThreadIds.add(t.id))
    );

    const mergedPages = newData.pages.map((newPage, pageIdx) => {
        const oldPage = oldData.pages[pageIdx];
        if (!oldPage) return newPage;

        const missing: { index: number; thread: Thread }[] = [];
        oldPage.data.results.forEach((thread, idx) => {
            if (optimisticIds.has(thread.id) && !newThreadIds.has(thread.id)) {
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

        return {
            ...newPage,
            data: {
                ...newPage.data,
                count: newPage.data.count + missing.length,
                results,
            },
        };
    });

    // Only drop optimistic IDs when the server actually reconfirmed them.
    // Two invocation paths reach this callback WITHOUT fresh server data and
    // must not trigger purges:
    //   1. `fetchNextPage`: existing pages come from the local cache unchanged,
    //      the server only returned the appended page.
    //   2. `setQueryData` optimistic patches: React Query runs structuralSharing
    //      on every cache write, including our own `_updateThreadAccessReadAt`
    //      calls. In that case old and new data carry the *same thread ids* —
    //      only the per-thread properties changed — so presence in newThreadIds
    //      is not a server confirmation, it is our own cache reflecting itself.
    const isFetchNextPage = newData.pages.length > oldData.pages.length;
    const oldThreadIds = new Set<string>();
    oldData.pages.forEach(page =>
        page.data.results.forEach(t => oldThreadIds.add(t.id))
    );
    const isLocalPatch = oldThreadIds.size === newThreadIds.size
        && [...oldThreadIds].every(id => newThreadIds.has(id));

    if (!isFetchNextPage && !isLocalPatch) {
        optimisticIds.forEach(id => {
            if (newThreadIds.has(id)) optimisticIds.delete(id);
        });
    }

    return { ...newData, pages: mergedPages };
};

/**
 * Drop trailing empty pages from an infinite query snapshot.
 *
 * Why: after a bulk mutation that shrinks the list (e.g. trash 25 threads
 * when only 40 were loaded across 2 pages), the server no longer has
 * enough data to fill all pages the client already cached. The 404 path
 * is converted by the query layer into an empty terminal page, which we
 * remove here so subsequent refetches stop targeting a non-existent page.
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
 * Compute the `is_unread` state a message must carry after a read-pointer
 * update. Mirrors the backend invariant `is_unread = created_at > read_at`,
 * with `read_at === null` meaning "everything unread".
 */
const deriveIsUnread = (createdAt: string, readAt: string | null): boolean => {
    if (readAt === null) return true;
    return createdAt > readAt;
};

/**
 * Apply an optimistic invalidation source to the messages cache of a thread.
 *
 * Pure counterpart of `queryClient.setQueryData(['messages', threadId], ...)`:
 * takes the current cache snapshot and returns the new one.
 *
 * When `source.readAt` is provided (including `null`), `is_unread` is derived
 * from the pointer itself — the pointer is authoritative. `source.payload` is
 * still merged for non-read-state fields (the per-message overrides stay
 * useful for e.g. `is_trashed`, `is_archived` mutations that piggyback on the
 * same invalidation channel).
 */
export const applyMessageUpdate = (
    oldData: messagesListResponse200 | undefined,
    threadId: Thread['id'],
    source: MessageQueryInvalidationSource,
): messagesListResponse200 | undefined => {
    if (!oldData?.data) return oldData;

    const targetedThreadIds = source.metadata.threadIds ?? [];
    const targetedIds = source.metadata.ids ?? [];

    let newResults: Message[] = [...oldData.data];

    if (source.type === 'delete') {
        newResults = newResults.filter((message) => {
            if (targetedThreadIds.includes(threadId)) return true;
            return !targetedIds.includes(message.id);
        });
    } else if (source.type === 'update') {
        const hasReadPointer = source.readAt !== undefined;

        newResults = newResults.map((message) => {
            const isTargeted =
                targetedThreadIds.includes(threadId) || targetedIds.includes(message.id);
            if (!isTargeted) return message;

            if (hasReadPointer) {
                const isUnread = deriveIsUnread(message.created_at!, source.readAt!);
                return { ...message, ...source.payload, is_unread: isUnread };
            }

            return { ...message, ...source.payload };
        });
    }

    return { ...oldData, data: newResults };
};

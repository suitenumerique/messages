import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useRef } from "react";
import { Mailbox, MailboxRoleChoices, Message, messagesListResponse200, PaginatedThreadList, Thread, ThreadEvent, useLabelsList, useMailboxesList, useMessagesList, useThreadsEventsList, useThreadsListInfinite, getThreadsEventsListQueryKey } from "../api/gen";
import { FetchStatus, InfiniteData, QueryStatus, RefetchOptions, useQueryClient } from "@tanstack/react-query";
import type { threadsListResponse } from "../api/gen/threads/threads";
import { useRouter } from "next/router";
import usePrevious from "@/hooks/use-previous";
import { useSearchParams } from "next/navigation";
import { MAILBOX_FOLDERS } from "../layouts/components/mailbox-panel/components/mailbox-list";
import { applyMessageUpdate, mergeOptimisticThreads, trimTrailingEmptyPages, type MessageQueryInvalidationSource } from "./mailbox-cache";
import { threadsList } from "../api/gen/threads/threads";
import { APIError } from "../api/api-error";

type QueryState = {
    status: QueryStatus,
    fetchStatus: FetchStatus,
    isFetching: boolean;
    isLoading: boolean;
}

type PaginatedQueryState = QueryState & {
    isFetchingNextPage: boolean;
}

export type TimelineItem =
    | { type: 'message'; data: Message; created_at: string }
    | { type: 'event'; data: ThreadEvent; created_at: string };

type MailboxContextType = {
    mailboxes: readonly Mailbox[] | null;
    threads: PaginatedThreadList | null;
    messages: readonly Message[] | null;
    threadEvents: readonly ThreadEvent[] | null;
    threadItems: readonly TimelineItem[] | null;
    selectedMailbox: Mailbox | null;
    selectedThread: Thread | null;
    unselectThread: () => void;
    loadNextThreads: () => Promise<unknown>;
    invalidateThreadMessages: (source?: MessageQueryInvalidationSource) => Promise<void>;
    invalidateThreadEvents: () => Promise<void>;
    invalidateThreadsStats: () => Promise<void>;
    invalidateLabels: () => Promise<void>;
    refetchMailboxes: (options?: RefetchOptions) => Promise<unknown>;
    isPending: boolean;
    queryStates: {
        mailboxes: QueryState,
        threads: PaginatedQueryState,
        messages: QueryState,
        threadEvents: QueryState,
    };
    error: {
        mailboxes: unknown | null;
        threads: unknown | null;
        messages: unknown | null;
        threadEvents: unknown | null;
    };
}

export const isThreadEvent = (item: TimelineItem | null): item is Extract<TimelineItem, { type: 'event' }> => item?.type === 'event';

/**
 * Canonical query key for the threads stats query.
 *
 * Single source of truth shared by:
 *   - query definition sites (`useThreadsStatsRetrieve` call sites that
 *     pass `queryParams` to scope the cache per filter/label)
 *   - invalidation / optimistic-update sites that target the whole
 *     per-mailbox stats subtree (omit `queryParams` for prefix matching)
 *
 * Keep in sync with the `invalidateThreadsStats` predicate, which relies
 * on `queryParams` being the last entry to filter out `label_slug=*` keys.
 */
export const getThreadsStatsQueryKey = (
    mailboxId: string | undefined,
    queryParams?: string,
) => {
    const base = ['threads', 'stats', mailboxId];
    return queryParams !== undefined ? [...base, queryParams] : base;
};

/** Minimal subset of URLSearchParams we read from. Accepts both
 *  `URLSearchParams` and Next's `ReadonlyURLSearchParams`. */
type ReadonlySearchParamsLike = {
    get: (key: string) => string | null;
    toString: () => string;
};

/**
 * Query key prefix for the threads LIST query of a mailbox.
 *
 * Used for invalidation and for prefix-matching optimistic updates
 * (`setQueriesData`) that should apply to every filter variant of
 * the same mailbox (list, search, all filter combinations…) in one shot.
 */
export const getMailboxThreadsListQueryKeyPrefix = (mailboxId: string | undefined) =>
    ['threads', mailboxId];

/**
 * Query key prefix for the SEARCH subtree of a mailbox's threads list.
 *
 * Matches every search variant of the mailbox (different filter combinations
 * applied on top of a search term). Used by the search cleanup effect to
 * purge or reset all search cache entries in one shot.
 */
export const getMailboxThreadsListSearchQueryKeyPrefix = (mailboxId: string | undefined) =>
    [...getMailboxThreadsListQueryKeyPrefix(mailboxId), 'search'];

/**
 * Full query key for a threads LIST query, disambiguated by filter.
 *
 * Key shape: `['threads', mailboxId, bucket, otherParams]`
 *   - `bucket`: `'search'` when a search term is active, `'list'` otherwise.
 *     This lets us target the whole search subtree by prefix without
 *     enumerating filter variants.
 *   - `otherParams`: stringified searchParams **without** the `search` value.
 *     Keeping non-search params in the key ensures that applying a filter
 *     (e.g. `has_unread=1`) while in search mode spawns a distinct cache
 *     entry instead of reusing stale pages from another filter variant.
 *
 * The search term itself is intentionally dropped from the key so that
 * typing in the search box mutates a single, stable entry per filter
 * combination — the cleanup effect then forces a refetch when the term
 * actually changes, avoiding a trail of orphaned cache entries.
 */
export const getMailboxThreadsListQueryKey = (
    mailboxId: string | undefined,
    searchParams: ReadonlySearchParamsLike,
) => {
    const prefix = getMailboxThreadsListQueryKeyPrefix(mailboxId);
    const normalized = new URLSearchParams(searchParams.toString());
    const hasSearch = Boolean(normalized.get('search'));
    if (hasSearch) normalized.delete('search');
    return [...prefix, hasSearch ? 'search' : 'list', normalized.toString()];
};

const MailboxContext = createContext<MailboxContextType>({
    mailboxes: null,
    threads: null,
    messages: null,
    threadEvents: null,
    threadItems: null,
    selectedMailbox: null,
    selectedThread: null,
    loadNextThreads: async () => {},
    unselectThread: () => {},
    invalidateThreadMessages: async () => {},
    invalidateThreadEvents: async () => {},
    invalidateThreadsStats: async () => {},
    invalidateLabels: async () => {},
    refetchMailboxes: async () => {},
    isPending: false,
    queryStates: {
        mailboxes: {
            status: 'pending',
            fetchStatus: 'idle',
            isFetching: false,
            isLoading: false,
        },
        threads: {
            status: 'pending',
            fetchStatus: 'idle',
            isFetching: false,
            isFetchingNextPage: false,
            isLoading: false,
        },
        messages: {
            status: 'pending',
            fetchStatus: 'idle',
            isFetching: false,
            isLoading: false,
        },
        threadEvents: {
            status: 'pending',
            fetchStatus: 'idle',
            isFetching: false,
            isLoading: false,
        },
    },
    error: {
        mailboxes: null,
        threads: null,
        messages: null,
        threadEvents: null,
    },
});

/**
 * MailboxProvider is a context provider for the mailbox context.
 * It provides the mailboxes, threads and messages to its children
 * It also provides callbacks to select a mailbox, thread or message
 */
export const MailboxProvider = ({ children }: PropsWithChildren) => {
    const queryClient = useQueryClient();
    const router = useRouter();
    const optimisticThreadIdsRef = useRef(new Set<string>());
    const searchParams = useSearchParams();
    const previousSearchParams = usePrevious(searchParams);
    const hasSearchParamsChanged = useMemo(() => {
        return previousSearchParams?.toString() !== searchParams.toString();
    }, [previousSearchParams, searchParams]);
    const mailboxQuery = useMailboxesList({
        query: {
            refetchInterval: 30 * 1000, // 30 seconds
            refetchOnWindowFocus: true,
        },
    });

    const selectedMailbox = useMemo(() => {
        if (!mailboxQuery.data?.data.length) return null;

        const mailboxId = router.query.mailboxId;
        const matched = mailboxQuery.data.data.find((mailbox) => mailbox.id === mailboxId);
        if (matched) return matched;

        return mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.admin)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.editor)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.sender)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.viewer)
            ?? mailboxQuery.data.data[mailboxQuery.data.data.length - 1]
    }, [router.query.mailboxId, mailboxQuery.data])

    const previousUnreadThreadsCount = usePrevious(selectedMailbox?.count_unread_threads);
    const previousDeliveringCount = usePrevious(selectedMailbox?.count_delivering);
    const previousUnreadMentionsCount = usePrevious(selectedMailbox?.count_unread_mentions);
    const threadQueryKey = useMemo(
        () => getMailboxThreadsListQueryKey(selectedMailbox?.id, searchParams),
        [selectedMailbox?.id, searchParams]
    );
    const threadsQuery = useThreadsListInfinite(undefined, {
        query: {
            enabled: !!selectedMailbox,
            initialPageParam: 1,
            queryKey: threadQueryKey,
            // `fetchNextPage` must stop at the true last page of the server.
            // Returning `undefined` is the React Query idiom for "no more
            // pages" — without it the hook would keep asking for pages the
            // backend has since dropped (bulk trash/archive shrinks the list).
            getNextPageParam: (lastPage, pages) => {
                if (lastPage?.data?.next === null) return undefined;
                return pages.length + 1;
            },
            // Intercept the 404 DRF raises for out-of-range pages. The list
            // may legitimately shrink between two refetches (e.g. user bulk
            // trashes threads), and React Query refetches every cached page
            // sequentially — a raw 404 on a trailing page would fail the
            // whole infinite query and flash an error toast. Convert it into
            // an empty terminal page so `trimTrailingEmptyPages` in
            // `structuralSharing` can drop it cleanly.
            queryFn: async ({ signal, pageParam }) => {
                try {
                    return await threadsList(
                        {
                            ...(router.query as Record<string, string>),
                            mailbox_id: selectedMailbox?.id ?? '',
                            page: pageParam as number,
                        },
                        { signal },
                    );
                } catch (error) {
                    const page = typeof pageParam === 'number' ? pageParam : 1;
                    if (error instanceof APIError && error.code === 404 && page > 1) {
                        return {
                            status: 200,
                            data: {
                                count: 0,
                                results: [],
                                next: null,
                                previous: page > 1 ? page - 1 : null,
                            } as PaginatedThreadList,
                            headers: new Headers(),
                        } as threadsListResponse;
                    }
                    throw error;
                }
            },
            // Merge-back optimistic threads filtered out by the server, then
            // drop trailing empty pages left over by shrunk result sets.
            structuralSharing: (oldData, newData) => {
                const merged = mergeOptimisticThreads(
                    oldData as InfiniteData<threadsListResponse> | undefined,
                    newData as InfiniteData<threadsListResponse>,
                    optimisticThreadIdsRef.current,
                );
                return trimTrailingEmptyPages(merged);
            },
        },
    });

    /**
     * Flatten the threads paginated query to a single result array
     */
    const flattenThreads = useMemo(() => {
        return threadsQuery.data?.pages.reduce((acc, page, index) => {
            const isLastPage = index === threadsQuery.data?.pages.length - 1;
            acc.results.push(...page.data.results);
            if (isLastPage) {
                acc.count = page.data.count;
                acc.next = page.data.next;
                acc.previous = page.data.previous;
            }
            return acc;
            }, {results: [], count: 0, next: null, previous: null} as PaginatedThreadList);
    }, [threadsQuery.data?.pages]);

    const selectedThread = useMemo(() => {
        const threadId = router.query.threadId;
        return flattenThreads?.results.find((thread) => thread.id === threadId) ?? null;
    }, [router.query.threadId, flattenThreads])
    const previousSelectedThreadMessagesCount = usePrevious(selectedThread?.messages.length);
    const previousSelectedThreadEventsCount = usePrevious(selectedThread?.events_count);

    const messagesQuery = useMessagesList({
        query: {
            enabled: !!selectedThread,
            queryKey: ['messages', selectedThread?.id],
        },
        request: {
            params: {
                thread_id: selectedThread?.id ?? '',
                mailbox_id: selectedMailbox?.id ?? '',
            }
        }
    });

    const threadEventsQuery = useThreadsEventsList(selectedThread?.id ?? '', {
        query: {
            enabled: !!selectedThread,
        },
    });

    const threadItems = useMemo<TimelineItem[] | null>(() => {
        if (!messagesQuery.data?.data) return null;
        const messageItems: TimelineItem[] = messagesQuery.data.data.map((m) => ({
            type: 'message' as const,
            data: m,
            created_at: m.created_at,
        }));
        const eventItems: TimelineItem[] = (threadEventsQuery.data?.data ?? []).map((e) => ({
            type: 'event' as const,
            data: e,
            created_at: e.created_at,
        }));
        return [...messageItems, ...eventItems].sort(
            (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        );
    }, [messagesQuery.data, threadEventsQuery.data?.data]);

    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox?.id ?? '' }, {
        query: {
            enabled: !!selectedMailbox,
        },
    });


    const _updateThreadMessagesQueryData = (threadId: Thread['id'], source: MessageQueryInvalidationSource) => {
        queryClient.setQueryData(['messages', threadId], (oldData: messagesListResponse200 | undefined) =>
            applyMessageUpdate(oldData, threadId, source)
        );
    }
    /**
     * Optimistically update ThreadAccess.read_at in the infinite threads cache
     * so ThreadItem sees the new read state immediately without waiting for re-fetch.
     */
    const _updateThreadAccessReadAt = (
        threadIds: Thread['id'][],
        mailboxId: string,
        readAt: string | null,
    ) => {
        queryClient.setQueriesData<InfiniteData<threadsListResponse>>(
            { queryKey: getMailboxThreadsListQueryKeyPrefix(mailboxId) },
            (oldData) => {
                if (!oldData) return oldData;
                return {
                    ...oldData,
                    pages: oldData.pages.map((page) => ({
                        ...page,
                        data: {
                            ...page.data,
                            results: page.data.results.map((thread) => {
                                if (!threadIds.includes(thread.id)) return thread;
                                return {
                                    ...thread,
                                    has_unread: thread.messaged_at
                                        ? (readAt === null || new Date(thread.messaged_at) > new Date(readAt))
                                        : false,
                                    accesses: thread.accesses.map((access) =>
                                        access.mailbox.id === mailboxId
                                            ? { ...access, read_at: readAt }
                                            : access
                                    ),
                                };
                            }),
                        },
                    })),
                };
            },
        );
    };

    /**
     * Optimistically update ThreadAccess.starred_at in the infinite threads cache
     * so ThreadItem sees the new starred state immediately without waiting for re-fetch.
     */
    const _updateThreadAccessStarredAt = (
        threadIds: Thread['id'][],
        mailboxId: string,
        starredAt: string | null,
    ) => {
        queryClient.setQueriesData<InfiniteData<threadsListResponse>>(
            { queryKey: getMailboxThreadsListQueryKeyPrefix(mailboxId) },
            (oldData) => {
                if (!oldData) return oldData;
                return {
                    ...oldData,
                    pages: oldData.pages.map((page) => ({
                        ...page,
                        data: {
                            ...page.data,
                            results: page.data.results.map((thread) => {
                                if (!threadIds.includes(thread.id)) return thread;
                                return {
                                    ...thread,
                                    has_starred: starredAt !== null,
                                    accesses: thread.accesses.map((access) =>
                                        access.mailbox.id === mailboxId
                                            ? { ...access, starred_at: starredAt }
                                            : access
                                    ),
                                };
                            }),
                        },
                    })),
                };
            },
        );
    };

    /**
     * Invalidate the threads and messages queries to refresh the data
     * If a source is provided, it could be used to update query cache from the source data
     */
    const invalidateThreadMessages = async (source?: MessageQueryInvalidationSource) => {
        // Optimistically patch caches before invalidating so the UI
        // renders the correct state immediately while re-fetches are in flight.
        if (source?.threadAccessReadAt) {
            const affectedThreadIds = source.metadata.threadIds ?? [];
            if (affectedThreadIds.length > 0) {
                _updateThreadAccessReadAt(
                    affectedThreadIds,
                    source.threadAccessReadAt.mailboxId,
                    source.threadAccessReadAt.readAt,
                );
            }
        }

        if (source?.threadAccessStarredAt) {
            const affectedThreadIds = source.metadata.threadIds ?? [];
            if (affectedThreadIds.length > 0) {
                _updateThreadAccessStarredAt(
                    affectedThreadIds,
                    source.threadAccessStarredAt.mailboxId,
                    source.threadAccessStarredAt.starredAt,
                );
            }
        }

        if (source && ((source.metadata.threadIds ?? []).length ?? 0) > 0) {
            source.metadata.threadIds!.forEach(threadId => {
                if (queryClient.getQueryState(['messages', threadId])) {
                    _updateThreadMessagesQueryData(threadId, source);
                }
            });
        }

        if (source && selectedThread && ((source.metadata.ids ?? []).length ?? 0) > 0) {
            _updateThreadMessagesQueryData(selectedThread.id, source);
        }

        if (source?.skipThreadsRefetch) {
            // Track these threads so structuralSharing merges them back on future refetches
            (source.metadata.threadIds ?? []).forEach(id =>
                optimisticThreadIdsRef.current.add(id)
            );
        } else {
            // Remove affected threads from optimistic tracking since the
            // server response is authoritative after a real refetch.
            (source?.metadata.threadIds ?? []).forEach(id =>
                optimisticThreadIdsRef.current.delete(id)
            );
            await queryClient.invalidateQueries({ queryKey: getMailboxThreadsListQueryKeyPrefix(selectedMailbox?.id) });
        }

        if (selectedThread) {
            await queryClient.invalidateQueries({ queryKey: ['messages', selectedThread.id] });
        }
    }

    const invalidateThreadEvents = async () => {
        if (selectedThread) {
            await queryClient.invalidateQueries({ queryKey: getThreadsEventsListQueryKey(selectedThread.id) });
        }
    }

    const invalidateThreadsStats = async () => {
        await queryClient.invalidateQueries({
            queryKey: getThreadsStatsQueryKey(selectedMailbox?.id),
            // Exclude per-label stats queries (`label_slug=…`) from the
            // fan-out: a global invalidation would otherwise trigger one
            // re-fetch per label in the sidebar, which is wasteful. Label
            // counts stay fresh via their own targeted refetch paths
            // (label mutations) and by the mailbox polling loop.
            predicate: ({ queryKey }) => !(queryKey[queryKey.length - 1] as string).startsWith('label_slug=')
        });
    }

    const invalidateLabels = async () => {
        await queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
    }

    /**
     * Unselect the current thread and navigate to the mailbox page if needed
     */
    const unselectThread = () => {
        if (typeof window === 'undefined') return;

        const threadId = router.query.threadId as string | undefined;
        if (selectedMailbox && threadId && window.location.pathname.includes(threadId)) {
            router.push(`/mailbox/${selectedMailbox!.id}${window.location.search}`);
        }
    }

    const context = {
        mailboxes: mailboxQuery.data?.data ?? null,
        threads: flattenThreads ?? null,
        messages: messagesQuery.data?.data ?? null,
        threadEvents: threadEventsQuery.data?.data ?? null,
        threadItems: threadItems,
        selectedMailbox,
        selectedThread,
        unselectThread,
        loadNextThreads: threadsQuery.fetchNextPage,
        invalidateThreadMessages,
        invalidateThreadEvents,
        invalidateThreadsStats,
        invalidateLabels,
        refetchMailboxes: mailboxQuery.refetch,
        isPending: mailboxQuery.isPending || threadsQuery.isPending || messagesQuery.isPending,
        queryStates: {
            mailboxes: {
                status: mailboxQuery.status,
                fetchStatus: mailboxQuery.fetchStatus,
                isFetching: mailboxQuery.isFetching,
                isLoading: mailboxQuery.isLoading,
            },
            threads: {
                status: threadsQuery.status,
                fetchStatus: threadsQuery.fetchStatus,
                isFetching: threadsQuery.isFetching,
                isFetchingNextPage: threadsQuery.isFetchingNextPage,
                isLoading: threadsQuery.isLoading,

            },
            messages: {
                status: messagesQuery.status,
                fetchStatus: messagesQuery.fetchStatus,
                isFetching: messagesQuery.isFetching,
                isLoading: messagesQuery.isLoading,
            },
            threadEvents: {
                status: threadEventsQuery.status,
                fetchStatus: threadEventsQuery.fetchStatus,
                isFetching: threadEventsQuery.isFetching,
                isLoading: threadEventsQuery.isLoading,
            },
        },
        error: {
            mailboxes: mailboxQuery.error,
            threads: threadsQuery.error,
            messages: messagesQuery.error,
            threadEvents: threadEventsQuery.error,
        },
    };

    useEffect(() => {
        if (selectedMailbox) {
            if (router.pathname === '/' ||  (selectedMailbox.id !== router.query.mailboxId && !router.pathname.includes('new'))) {
                const defaultFolder = MAILBOX_FOLDERS()[0];
                const hash = window.location.hash;
                if (router.query.threadId) {
                    router.replace(`/mailbox/${selectedMailbox.id}/thread/${router.query.threadId}?${router.query.search}${hash}`);
                } else {
                    router.replace(`/mailbox/${selectedMailbox.id}?${new URLSearchParams(defaultFolder.filter).toString()}${hash}`);
                }
                invalidateThreadMessages();
            }
        }
    }, [selectedMailbox]);

    useEffect(() => {
        if (selectedMailbox && !selectedThread) {
            const threadId = router.query.threadId;
            const thread = flattenThreads?.results.find((thread) => thread.id === threadId);
            if (thread) {
                router.replace(`/mailbox/${selectedMailbox.id}/thread/${thread.id}?${searchParams}`);
            }
        }
    }, [flattenThreads]);

    // Invalidate the threads query when mailbox stats change (unread messages,
    // delivering count or unread mentions)
    useEffect(() => {
        if (!selectedMailbox) return;

        const hasUnreadCountChanged =
            previousUnreadThreadsCount !== undefined &&
            previousUnreadThreadsCount !== selectedMailbox.count_unread_threads;

        const hasDeliveringCountChanged =
            previousDeliveringCount !== undefined &&
            previousDeliveringCount !== selectedMailbox.count_delivering;

        const hasUnreadMentionsCountChanged =
            previousUnreadMentionsCount !== undefined &&
            previousUnreadMentionsCount !== selectedMailbox.count_unread_mentions;

        if (hasUnreadCountChanged || hasDeliveringCountChanged || hasUnreadMentionsCountChanged) {
            invalidateThreadsStats();
            queryClient.invalidateQueries({ queryKey: getMailboxThreadsListQueryKeyPrefix(selectedMailbox?.id) });
        }
    }, [selectedMailbox?.count_unread_threads, selectedMailbox?.count_delivering, selectedMailbox?.count_unread_mentions]);

    // Invalidate the thread messages query to refresh the thread messages when there is a new message
    useEffect(() => {
        if (!selectedThread || previousSelectedThreadMessagesCount === undefined) return;
        if (previousSelectedThreadMessagesCount < (selectedThread?.messages.length ?? 0)) {
            invalidateThreadMessages();
        }
    }, [selectedThread?.messages.length]);

    // Invalidate the thread events query to refresh the thread events when a new
    // event (e.g. a mention) is added to the currently open thread.
    useEffect(() => {
        if (!selectedThread || previousSelectedThreadEventsCount === undefined) return;
        if (previousSelectedThreadEventsCount < (selectedThread?.events_count ?? 0)) {
            invalidateThreadEvents();
        }
    }, [selectedThread?.events_count]);

    // Unselect the thread when it no longer has any messages (e.g. after
    // sending the only draft in the thread).
    useEffect(() => {
        if (!selectedThread) return;
        const messages = messagesQuery.data?.data;
        if (messages && messages.length === 0) {
            unselectThread();
        }
    }, [messagesQuery.data?.data]);

    // Clear optimistic thread IDs when filters or mailbox change so the next
    // refetch shows the pure server-side list.
    useEffect(() => {
        optimisticThreadIdsRef.current.clear();
    }, [selectedMailbox?.id, searchParams.toString()]);

    useEffect(() => {
        const previousSearch = previousSearchParams?.get('search');
        const currentSearch = searchParams.get('search');

        if (previousSearch && !currentSearch) {
            // Exiting search mode: purge every cached search variant so
            // re-entering search doesn't briefly flash stale results from
            // the previous query (prefix match covers all filter variants).
            queryClient.removeQueries({
                queryKey: getMailboxThreadsListSearchQueryKeyPrefix(selectedMailbox?.id),
            });
        } else if (previousSearch && currentSearch && currentSearch !== previousSearch) {
            // Search term changed while already in search mode: the query key
            // intentionally omits the search term so React Query would reuse
            // the cache — reset every search variant to force a refetch with
            // the new term.
            queryClient.resetQueries({
                queryKey: getMailboxThreadsListSearchQueryKeyPrefix(selectedMailbox?.id),
            });
        }

        unselectThread();
    }, [hasSearchParamsChanged])

    return <MailboxContext.Provider value={context}>{children}</MailboxContext.Provider>
};

export const useMailboxContext = () => {
    const context = useContext(MailboxContext);
    if (!context) {
        throw new Error("`useMailboxContext` must be used within a children of `MailboxProvider`.");
    }
    return context;
}

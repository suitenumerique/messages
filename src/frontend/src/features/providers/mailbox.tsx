import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useRef } from "react";
import { Mailbox, MailboxRoleChoices, Message, messagesListResponse200, PaginatedThreadList, Thread, ThreadEvent, useLabelsList, useMailboxesList, useMessagesList, useThreadsEventsList, useThreadsListInfinite, getThreadsEventsListQueryKey } from "../api/gen";
import { FetchStatus, InfiniteData, QueryStatus, RefetchOptions, useQueryClient } from "@tanstack/react-query";
import type { threadsListResponse } from "../api/gen/threads/threads";
import { useRouter } from "next/router";
import usePrevious from "@/hooks/use-previous";
import { useSearchParams } from "next/navigation";
import { MAILBOX_FOLDERS } from "../layouts/components/mailbox-panel/components/mailbox-list";

type QueryState = {
    status: QueryStatus,
    fetchStatus: FetchStatus,
    isFetching: boolean;
    isLoading: boolean;
}

type PaginatedQueryState = QueryState & {
    isFetchingNextPage: boolean;
}

type MessageQueryInvalidationSource = {
    type: 'delete' | 'update';
    metadata: { ids?: Message['id'][], threadIds?: Thread['id'][] };
    payload?: Partial<Message>;
    /** When updating read state, optimistically patch ThreadAccess.read_at in the threads cache. */
    threadAccessReadAt?: { mailboxId: string; readAt: string | null };
    /** Optimistically patch ThreadAccess.starred_at in the threads cache. */
    threadAccessStarredAt?: { mailboxId: string; starredAt: string | null };
    /**
     * When set, only messages created at or before this timestamp
     * will receive the payload update (used for read pointer).
     * Messages after this date keep their current state.
     */
    readAt?: string | null;
    /** When true, skip the threads list refetch (rely on optimistic cache only). */
    skipThreadsRefetch?: boolean;
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
        return mailboxQuery.data?.data.find((mailbox) => mailbox.id === mailboxId)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.admin)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.editor)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.sender)
            ?? mailboxQuery.data.data.findLast(m => m.role === MailboxRoleChoices.viewer)
            ?? mailboxQuery.data.data[mailboxQuery.data.data.length - 1]
    }, [router.query.mailboxId, mailboxQuery.data])

    const previousUnreadThreadsCount = usePrevious(selectedMailbox?.count_unread_threads);
    const previousDeliveringCount = usePrevious(selectedMailbox?.count_delivering);
    const threadQueryKey = useMemo(() => {
        const queryKey = ['threads', selectedMailbox?.id];
        if (searchParams.get('search')) {
            return [...queryKey, 'search'];
        }
        return [...queryKey, searchParams.toString()];
    }, [selectedMailbox?.id, searchParams]);
    const threadsQuery = useThreadsListInfinite(undefined, {
        query: {
            enabled: !!selectedMailbox,
            initialPageParam: 1,
            queryKey: threadQueryKey,
            getNextPageParam: (lastPage, pages) => {
                return pages.length + 1;
            },
            /**
             * Merge-back optimistic threads on refetch.
             *
             * Problem: when a filter is active (e.g. "unread" or "starred"),
             * a read/starred mutation optimistically patches the thread in
             * cache but skips the list refetch (`skipThreadsRefetch`). Later,
             * when a refetch does happen (polling, navigation…), the server
             * no longer returns that thread (it no longer matches the filter)
             * → it would vanish from the UI.
             *
             * Solution: `structuralSharing` runs *before* React re-renders.
             * It compares old cache (with optimistic threads) to the new
             * server response. Any thread tracked in `optimisticThreadIdsRef`
             * that is missing from the server response is re-inserted at its
             * original position so the user sees no flash.
             *
             * Lifecycle of an optimistic thread ID:
             * - Added to the set by `invalidateThreadMessages({ skipThreadsRefetch })`
             * - Removed from the set here when the server response includes it
             *   (meaning the server still considers it valid for the current query)
             * - Cleared entirely when the user changes filters or mailbox
             *   (via the cleanup `useEffect` on `selectedMailbox?.id` / `searchParams`)
             */
            structuralSharing: (oldData, newData) => {
                const optimisticIds = optimisticThreadIdsRef.current;
                if (!oldData || optimisticIds.size === 0) return newData;

                const oldInfinite = oldData as InfiniteData<threadsListResponse>;
                const newInfinite = newData as InfiniteData<threadsListResponse>;

                // 1. Build flat index of old thread positions to restore ordering later
                const oldOrderedIds: string[] = [];
                oldInfinite.pages.forEach(page =>
                    page.data.results.forEach(t => oldOrderedIds.push(t.id))
                );

                // 2. Collect all thread IDs the server returned
                const newThreadIds = new Set<string>();
                newInfinite.pages.forEach(page =>
                    page.data.results.forEach(t => newThreadIds.add(t.id))
                );

                // 3. Identify optimistic threads the server filtered out,
                //    remembering their original flat index for position-preserving re-insertion
                const missingByOldIndex = new Map<number, Thread>();
                oldInfinite.pages.forEach(page =>
                    page.data.results.forEach(thread => {
                        if (optimisticIds.has(thread.id) && !newThreadIds.has(thread.id)) {
                            missingByOldIndex.set(oldOrderedIds.indexOf(thread.id), thread);
                        }
                    })
                );

                // 4. Stop protecting threads the server still returns
                //    (they don't need merge-back anymore)
                optimisticIds.forEach(id => {
                    if (newThreadIds.has(id)) optimisticIds.delete(id);
                });

                if (missingByOldIndex.size === 0) return newData;

                // 5. Flatten new server results then splice missing threads
                //    back at their original positions (sorted ascending so
                //    earlier splices don't shift later indices)
                const flatNewResults: Thread[] = [];
                newInfinite.pages.forEach(page =>
                    flatNewResults.push(...page.data.results)
                );

                const sortedEntries = [...missingByOldIndex.entries()].sort(([a], [b]) => a - b);
                for (const [originalIndex, thread] of sortedEntries) {
                    const insertAt = Math.min(originalIndex, flatNewResults.length);
                    flatNewResults.splice(insertAt, 0, thread);
                }

                // 6. Return merged results in page 1
                return {
                    ...newInfinite,
                    pages: newInfinite.pages.map((page, i) => {
                        if (i !== 0) return page;
                        return {
                            ...page,
                            data: {
                                ...page.data,
                                count: page.data.count + missingByOldIndex.size,
                                results: flatNewResults,
                            },
                        };
                    }),
                };
            },
        },
        request: {
            params: {
                ...(router.query as Record<string, string>),
                mailbox_id: selectedMailbox?.id ?? '',
            }
        }
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
        queryClient.setQueryData(['messages', threadId], (oldData: messagesListResponse200 | undefined) => {
            if (!oldData?.data) return oldData;
            let newResults = [ ...oldData.data ];
            if (source.type === 'delete') {
                newResults = newResults.filter((message: Message) => {
                    if ((source.metadata.threadIds ?? []).includes(threadId)) return true;
                    return !(source.metadata.ids ?? []).includes(message.id);
                });
            } else if (source.type === 'update') {
                newResults = newResults.map((message: Message) => {
                    const isTargeted =
                        (source.metadata.threadIds ?? []).includes(threadId)
                        || (source.metadata.ids ?? []).includes(message.id);

                    if (!isTargeted) return message;

                    // When a readAt pointer is provided, only update messages
                    // created at or before that timestamp. When readAt is null
                    // (mark all unread), update every message.
                    if (source.readAt !== undefined && source.readAt !== null) {
                        if (message.created_at > source.readAt) return message;
                    }

                    return { ...message, ...source.payload };
                });
            }

            return {...oldData, data: newResults};
        });
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
            { queryKey: ['threads', mailboxId] },
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
            { queryKey: ['threads', mailboxId] },
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
            await queryClient.invalidateQueries({ queryKey: ['threads', selectedMailbox?.id] });
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
            queryKey: ['threads', 'stats', selectedMailbox?.id],
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

    // Invalidate the threads query when mailbox stats change (unread messages or delivering count)
    useEffect(() => {
        if (!selectedMailbox) return;

        const hasUnreadCountChanged =
            previousUnreadThreadsCount !== undefined &&
            previousUnreadThreadsCount !== selectedMailbox.count_unread_threads;

        const hasDeliveringCountChanged =
            previousDeliveringCount !== undefined &&
            previousDeliveringCount !== selectedMailbox.count_delivering;

        if (hasUnreadCountChanged || hasDeliveringCountChanged) {
            invalidateThreadsStats();
            queryClient.invalidateQueries({ queryKey: ['threads', selectedMailbox?.id] });
        }
    }, [selectedMailbox?.count_unread_threads, selectedMailbox?.count_delivering]);

    // Invalidate the thread messages query to refresh the thread messages when there is a new message
    useEffect(() => {
        if (!selectedThread || previousSelectedThreadMessagesCount === undefined) return;
        if (previousSelectedThreadMessagesCount < (selectedThread?.messages.length ?? 0)) {
            invalidateThreadMessages();
        }
    }, [selectedThread?.messages.length]);

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
            // Exiting search mode: purge cached search results so re-entering
            // search doesn't briefly flash stale results from the previous query.
            queryClient.removeQueries({
                queryKey: ['threads', selectedMailbox?.id, 'search'],
                exact: true,
            });
        } else if (previousSearch && currentSearch && currentSearch !== previousSearch) {
            // Search term changed while already in search mode:
            // reset the query to force a refetch with the new params.
            queryClient.resetQueries({ queryKey: ['threads', selectedMailbox?.id, 'search'] });
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

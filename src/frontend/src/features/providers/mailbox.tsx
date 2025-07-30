import { createContext, PropsWithChildren, useContext, useEffect, useMemo } from "react";
import { Mailbox, PaginatedMessageList, PaginatedThreadList, Thread, ThreadsListParams, useLabelsList, useMailboxesList, useMessagesList, useThreadsListInfinite } from "../api/gen";
import { FetchStatus, QueryStatus, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/router";
import usePrevious from "@/hooks/use-previous";
import { useSearchParams } from "next/navigation";
import { MAILBOX_FOLDERS } from "../layouts/components/mailbox-panel/components/mailbox-list";
import { useDebounceCallback } from "@/hooks/use-debounce-callback";

type QueryState = {
    status: QueryStatus,
    fetchStatus: FetchStatus,
    isFetching: boolean;
    isLoading: boolean;
}

type PaginatedQueryState = QueryState & {
    isFetchingNextPage: boolean;
}

type MailboxContextType = {
    mailboxes: readonly Mailbox[] | null;
    threads: PaginatedThreadList | null;
    messages: PaginatedMessageList | null;
    selectedMailbox: Mailbox | null;
    selectedThread: Thread | null;
    unselectThread: () => void;
    loadNextThreads: () => Promise<unknown>;
    invalidateThreadMessages: () => void;
    invalidateThreadsStats: () => void;
    invalidateLabels: () => void;
    refetchMailboxes: () => void;
    isPending: boolean;
    queryStates: {
        mailboxes: QueryState,
        threads: PaginatedQueryState,
        messages: QueryState,
    };
    error: {
        mailboxes: unknown | null;
        threads: unknown | null;
        messages: unknown | null;
    };
}

const MailboxContext = createContext<MailboxContextType>({
    mailboxes: null,
    threads: null,
    messages: null,
    selectedMailbox: null,
    selectedThread: null,
    loadNextThreads: async () => {},
    unselectThread: () => {},
    invalidateThreadMessages: () => {},
    invalidateThreadsStats: () => {},
    invalidateLabels: () => {},
    refetchMailboxes: () => {},
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
    },
    error: {
        mailboxes: null,
        threads: null,
        messages: null,
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
    const searchParams = useSearchParams();
    const previousSearchParams = usePrevious(searchParams);
    const hasSearchParamsChanged = useMemo(() => {
        return previousSearchParams?.toString() !== searchParams.toString();
    }, [previousSearchParams, searchParams]);
    const mailboxQuery = useMailboxesList({
        query: {
            refetchInterval: 30 * 1000, // 30 seconds
        },
    });
    const selectedMailbox = useMemo(() => {
        const mailboxId = router.query.mailboxId;
        return mailboxQuery.data?.data.find((mailbox) => mailbox.id === mailboxId) ?? mailboxQuery.data?.data[0] ?? null;
    }, [router.query.mailboxId, mailboxQuery.data])

    const previousUnreadMessagesCount = usePrevious(selectedMailbox?.count_unread_messages || 0);
    
    // Build thread list params from router query
    const threadListParams = useMemo(() => {
        const params: ThreadsListParams = {
            ...(router.query as Record<string, string>),
            mailbox_id: selectedMailbox?.id ?? '',
        };
        // Remove non-thread params
        delete (params as any).mailboxId;
        delete (params as any).threadId;
        
        // Debug: Log the thread list params when message_ids is present
        if (params.message_ids) {
            console.log('Mailbox Provider: Sending API request with message_ids:', params.message_ids);
            console.log('Mailbox Provider: Full thread list params:', params);
            console.log('Mailbox Provider: API URL will be constructed with these params - checking for conflicting search param:', {
                has_search_param: !!params.search,
                search_value: params.search,
                has_message_ids: !!params.message_ids,
                message_ids_value: params.message_ids
            });
        }
        
        return params;
    }, [router.query, selectedMailbox?.id]);
    
    const threadQueryKey = useMemo(() => {
        const queryKey = ['threads', selectedMailbox?.id];
        // Use router.query to ensure consistency with threadListParams
        if (router.query.search) {
            return [...queryKey, 'search'];
        }
        // Handle AI search results with message_ids
        if (router.query.message_ids) {
            return [...queryKey, 'ai-search', router.query.message_ids];
        }
        // Create a consistent query key based on the actual params being sent
        const paramsString = new URLSearchParams(
            Object.entries(threadListParams)
                .filter(([_, value]) => value !== undefined && value !== '')
                .map(([key, value]) => [key, String(value)])
        ).toString();
        return [...queryKey, paramsString];
    }, [selectedMailbox?.id, router.query, threadListParams]);

    const threadsQuery = useThreadsListInfinite(threadListParams, {
        query: {
            enabled: !!selectedMailbox,
            initialPageParam: 1,
            queryKey: threadQueryKey,
            // Force fresh data when message_ids is present
            staleTime: threadListParams.message_ids ? 0 : 30000,
            getNextPageParam: (_lastPage: any, pages: any[]) => {
                return pages.length + 1;
            },
        },
    });

    // Debug: Log query key changes when message_ids is present
    useEffect(() => {
        if (threadListParams.message_ids) {
            console.log('Mailbox Provider: Query key changed with message_ids:', {
                threadQueryKey,
                threadListParams,
                query_status: threadsQuery.status,
                data_pages: threadsQuery.data?.pages.length,
                first_page_results: threadsQuery.data?.pages[0]?.data.results.length
            });
            
            // Clear all thread-related cache when switching to AI search
            queryClient.invalidateQueries({ queryKey: ['threads', selectedMailbox?.id] });
            
            // Force query refetch when message_ids changes to ensure fresh data
            threadsQuery.refetch();
        }
    }, [threadQueryKey, threadListParams.message_ids, selectedMailbox?.id, queryClient]);
    
    // Separate effect to log query status changes
    useEffect(() => {
        if (threadListParams.message_ids && threadsQuery.status && threadsQuery.data) {
            console.log('Mailbox Provider: Query status/data changed:', {
                status: threadsQuery.status,
                data_available: !!threadsQuery.data,
                pages_count: threadsQuery.data?.pages.length,
                total_results: threadsQuery.data?.pages[0]?.data.results.length
            });
        }
    }, [threadsQuery.status, threadsQuery.data, threadListParams.message_ids]);

    // Debug: Log when threads are successfully fetched with message_ids
    useEffect(() => {
        if (threadListParams.message_ids && threadsQuery.data) {
            console.log('Mailbox Provider: Successfully fetched threads with message_ids:', threadListParams.message_ids);
            console.log('Mailbox Provider: Fetched threads count:', threadsQuery.data.pages[0]?.data.count);
            console.log('Mailbox Provider: Thread query key:', threadQueryKey);
            console.log('Mailbox Provider: Query status:', threadsQuery.status);
            console.log('Mailbox Provider: Query error:', threadsQuery.error);
        }
    }, [threadListParams.message_ids, threadsQuery.data, threadQueryKey, threadsQuery.status, threadsQuery.error]);

    /**
     * Flatten the threads paginated query to a single result array
     */
    const flattenThreads = useMemo(() => {
        const result = threadsQuery.data?.pages.reduce((acc, page, index) => {
            const isLastPage = index === threadsQuery.data?.pages.length - 1;
            acc.results.push(...page.data.results);
            if (isLastPage) {
                acc.count = page.data.count;
                acc.next = page.data.next;
                acc.previous = page.data.previous;
            }
            return acc;
            }, {results: [], count: 0, next: null, previous: null} as PaginatedThreadList);
        
        // Debug: Log the flattened threads when message_ids is present
        if (threadListParams.message_ids && result) {
            console.log('Mailbox Provider: Flattened threads with message_ids filter:', {
                message_ids: threadListParams.message_ids,
                total_threads: result.results.length,
                thread_ids: result.results.map(t => t.id),
                first_thread_subject: result.results[0]?.subject || 'No subject',
                query_key: threadQueryKey,
                raw_pages_data: threadsQuery.data?.pages.map(p => ({
                    results_count: p.data.results.length,
                    count: p.data.count
                }))
            });
        }
        
        return result;
    }, [threadsQuery.data?.pages, threadListParams.message_ids]);

    const selectedThread = useMemo(() => {
        const threadId = router.query.threadId;
        return threadsQuery.data?.pages.flatMap((page) => page.data.results).find((thread) => thread.id === threadId) ?? null;
    }, [router.query.threadId, flattenThreads])

    const messagesQuery = useMessagesList(undefined, {
        query: {
            enabled: !!selectedThread,
            queryKey: ['messages', selectedThread?.id],
        },
        request: {
            params: {
                thread_id: selectedThread?.id ?? ''
            }
        }
    });

    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox?.id ?? '' }, {
        query: {
            enabled: !!selectedMailbox,
        },
    });

    /**
     * Invalidate the threads and messages queries to refresh the data
     */
    const invalidateThreadMessages = async () => {
        await queryClient.invalidateQueries({ queryKey: ['threads', selectedMailbox?.id] });
        if (selectedThread) {
            await queryClient.invalidateQueries({ queryKey: ['messages', selectedThread.id] });
        }
    }
    const resetSearchQueryDebounced = useDebounceCallback(() => {
        queryClient.resetQueries(
            { predicate: ({ queryKey}) => queryKey.includes('search') },
        );
    }, 500);

    const invalidateThreadsStats = async () => {
        await queryClient.invalidateQueries({ queryKey: ['threads', 'stats', selectedMailbox?.id] });
    }

    const invalidateLabels = async () => {
        await queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
    }

    /**
     * Unselect the current thread and navigate to the mailbox page if needed
     */
    const unselectThread = () => {
        if (selectedMailbox && router.query.threadId) {
            // Build query string from router.query to ensure consistency
            const queryString = new URLSearchParams();
            Object.entries(router.query).forEach(([key, value]) => {
                if (key !== 'mailboxId' && key !== 'threadId' && value) {
                    queryString.set(key, String(value));
                }
            });
            router.push(`/mailbox/${selectedMailbox!.id}?${queryString.toString()}`);
        }
    }

    const context = useMemo(() => ({
        mailboxes: mailboxQuery.data?.data ?? null,
        threads: flattenThreads ?? null,
        messages: messagesQuery.data?.data ?? null,
        selectedMailbox,
        selectedThread,
        unselectThread,
        loadNextThreads: threadsQuery.fetchNextPage,
        invalidateThreadMessages,
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
        },
        error: {
            mailboxes: mailboxQuery.error,
            threads: threadsQuery.error,
            messages: messagesQuery.error,
        }
    }), [
        mailboxQuery,
        threadsQuery,
        messagesQuery,
        selectedMailbox,
        selectedThread,
    ]);

    useEffect(() => {
        if (selectedMailbox) {
            if (router.pathname === '/' ||  (selectedMailbox.id !== router.query.mailboxId && !router.pathname.includes('new'))) {
                const defaultFolder = MAILBOX_FOLDERS[0];
                const hash = window.location.hash;
                if (router.query.threadId) {
                    router.replace(`/mailbox/${selectedMailbox.id}/thread/${router.query.threadId}?${router.query.search}${hash}`);
                } else {
                    // Preserve existing query parameters (including message_ids from AI search)
                    const currentParams = new URLSearchParams();
                    Object.entries(router.query).forEach(([key, value]) => {
                        if (key !== 'mailboxId' && key !== 'threadId' && value) {
                            currentParams.set(key, String(value));
                        }
                    });
                    
                    // Debug: Log when redirecting to preserve search params
                    if (currentParams.has('message_ids')) {
                        console.log('Mailbox Provider: Preserving message_ids during redirect:', currentParams.get('message_ids'));
                    }
                    
                    // Only add default folder filter if no search parameters exist
                    if (currentParams.toString() === '') {
                        if (defaultFolder.filter) {
                            Object.entries(defaultFolder.filter).forEach(([key, value]) => {
                                currentParams.set(key, value);
                            });
                        }
                    }
                    
                    router.replace(`/mailbox/${selectedMailbox.id}?${currentParams.toString()}${hash}`);
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
                // Build query string from router.query to ensure consistency
                const queryString = new URLSearchParams();
                Object.entries(router.query).forEach(([key, value]) => {
                    if (key !== 'mailboxId' && value) {
                        queryString.set(key, String(value));
                    }
                });
                router.replace(`/mailbox/${selectedMailbox.id}/thread/${thread.id}?${queryString.toString()}`);
            }
        }
    }, [flattenThreads]);

    // Invalidate the threads query to refresh the threads list when the unread messages count changes
    useEffect(() => {
        if ((previousUnreadMessagesCount ?? 0) > (selectedMailbox?.count_unread_messages ?? 0)) {
            queryClient.invalidateQueries({ queryKey: ['threads', selectedMailbox?.id] });
        }
    }, [selectedMailbox?.count_unread_messages]);

    useEffect(() => {
        if (searchParams.get('search') !== previousSearchParams?.get('search')) {
            resetSearchQueryDebounced();
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

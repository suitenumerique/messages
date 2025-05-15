import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";
import { Mailbox, PaginatedMessageList, PaginatedThreadList, Thread, useMailboxesList, useMessagesList, useThreadsListInfinite } from "../api/gen";
import { FetchStatus, QueryStatus, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/router";
import usePrevious from "@/hooks/use-previous";
import { useSearchParams } from "next/navigation";
import { DEFAULT_FOLDERS } from "../layouts/components/mailbox-panel/components/mailbox-list";
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

    /**
     * Unselect the current thread and navigate to the mailbox page if needed
     */
    const unselectThread = () => {
        if (selectedMailbox && router.query.threadId) {
            router.push(`/mailbox/${selectedMailbox!.id}?${searchParams}`);
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
                const defaultFolder = DEFAULT_FOLDERS[0];
                if (router.query.threadId) {
                    router.replace(`/mailbox/${selectedMailbox.id}/thread/${router.query.threadId}?${router.query.search}`);
                } else {
                    router.replace(`/mailbox/${selectedMailbox.id}?${new URLSearchParams(defaultFolder.filter).toString()}`);
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
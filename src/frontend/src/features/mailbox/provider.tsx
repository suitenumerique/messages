import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";
import { Mailbox, PaginatedMessageList, PaginatedThreadList, Thread, useMailboxesList, useMessagesList, useThreadsList } from "../api/gen";
import { FetchStatus, QueryStatus, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/router";
import usePrevious from "@/hooks/usePrevious";

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
    selectMailbox: (mailbox: Mailbox) => void;
    selectedThread: Thread | null;
    selectThread: (thread: Thread | null) => void;
    unselectThread: () => void;
    invalidateThreadMessages: () => void;
    refetchMailboxes: () => void;
    isPending: boolean;
    queryStates: {
        mailboxes: QueryState,
        threads: QueryState,
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
    selectMailbox: () => {},
    selectedThread: null,
    selectThread: () => {},
    unselectThread: () => {},
    invalidateThreadMessages: () => {},
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
    const [selectedMailbox, setSelectedMailbox] = useState<Mailbox | null>(null);
    const [selectedThread, setSelectedThread] = useState<Thread | null>(null);
    const mailboxQuery = useMailboxesList({
        query: {
            refetchInterval: 30 * 1000, // 30 seconds
        },
    });
    const previousUnreadMessagesCount = usePrevious(selectedMailbox?.count_unread_messages || 0);
    const threadsQuery = useThreadsList(undefined, {
        query: {
            enabled: !!selectedMailbox,
        },
        request: {
            params: {
                mailbox_id: selectedMailbox?.id ?? '',
            }
        }
    });
    const messagesQuery = useMessagesList(undefined, {
        query: {
            enabled: !!selectedThread,
            queryKey: ['messages', selectedThread?.id],
        },
        request: {
            params: {
                thread_id: selectedThread?.id ?? '',
            }
        }
    });

    /**
     * Invalidate the threads and messages queries to refresh the data
     */
    const invalidateThreadMessages = async () => {
        await queryClient.invalidateQueries({ queryKey: ['/api/v1.0/threads/'] });
        if (selectedThread) {
            await queryClient.invalidateQueries({ queryKey: ['messages', selectedThread.id] });
        }
    }

    /**
     * Unselect the current thread and navigate to the mailbox page if needed
     */
    const unselectThread = () => {
        setSelectedThread(null);
        if (router.query.threadId) {
            router.push(`/mailbox/${selectedMailbox!.id}`);
        }
    }

    const context = useMemo(() => ({
        mailboxes: mailboxQuery.data?.data ?? null,
        threads: threadsQuery.data?.data ?? null,
        messages: messagesQuery.data?.data ?? null,
        selectedMailbox,
        selectMailbox: setSelectedMailbox,
        selectedThread,
        unselectThread,
        selectThread: setSelectedThread,
        invalidateThreadMessages,
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
        const mailboxes = mailboxQuery.data?.data;
        if (mailboxes && mailboxes.length > 0) {
            const mailboxId = router.query.mailboxId;
            setSelectedMailbox(mailboxes.find((mailbox) => mailbox.id === mailboxId) ?? mailboxes[0]);
        }
    }, [mailboxQuery.data?.data]);

    useEffect(() => {
        if (selectedMailbox) {
            if (router.pathname === '/' || selectedMailbox.id !== router.query.mailboxId) {
                router.replace(`/mailbox/${selectedMailbox.id}`);
                invalidateThreadMessages();
            }
        }
    }, [selectedMailbox]);

    useEffect(() => {
        if (selectedMailbox && !selectedThread) {
            const threadId = router.query.threadId;
            const thread = threadsQuery.data?.data?.results.find((thread) => thread.id === threadId);
            if (thread) {
                setSelectedThread(thread);
                router.replace(`/mailbox/${selectedMailbox.id}/thread/${thread.id}`);
            }
        }
    }, [threadsQuery.data?.data]);

    useEffect(() => {
        if (selectedThread) {
            const threads = threadsQuery.data?.data?.results;
            const newSelectedThread = threads?.find((thread) => thread.id === selectedThread?.id);
            if (newSelectedThread) {
                if (newSelectedThread?.updated_at !== selectedThread?.updated_at) {
                    setSelectedThread(newSelectedThread);
                    messagesQuery.refetch();
                }
            }
        }
    }, [threadsQuery.data?.data?.results, selectedThread]);

    // Invalidate the threads query to refresh the threads list when the unread messages count changes
    useEffect(() => {
        if (previousUnreadMessagesCount !== selectedMailbox?.count_unread_messages) {
            queryClient.invalidateQueries({ queryKey: ['/api/v1.0/threads/'] });
        }
    }, [selectedMailbox?.count_unread_messages]);

    return <MailboxContext.Provider value={context}>{children}</MailboxContext.Provider>
};

export const useMailboxContext = () => {
    const context = useContext(MailboxContext);
    if (!context) {
        throw new Error("`useMailboxContext` must be used within a children of `MailboxProvider`.");
    }
    return context;
}
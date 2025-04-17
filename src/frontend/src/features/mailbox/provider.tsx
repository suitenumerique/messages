import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";
import { Mailbox, PaginatedMessageList, PaginatedThreadList, Thread, useMailboxesList, useMessagesList, useThreadsList } from "../api/gen";

type MailboxContextType = {
    mailboxes: readonly Mailbox[] | null;
    threads: PaginatedThreadList | null;
    messages: PaginatedMessageList | null;
    selectedMailbox: Mailbox | null;
    selectMailbox: (mailbox: Mailbox) => void;
    selectedThread: Thread | null;
    selectThread: (thread: Thread | null) => void;
    isPending: boolean;
    status: {
        mailboxes: 'pending' | 'error' | 'success' | null,
        threads: 'pending' | 'error' | 'success' | null,
        messages: 'pending' | 'error' | 'success' | null,
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
    isPending: false,
    status: {
        mailboxes: null,
        threads: null,
        messages: null,
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
    const [selectedMailbox, setSelectedMailbox] = useState<Mailbox | null>(null);
    const [selectedThread, setSelectedThread] = useState<Thread | null>(null);
    const mailboxQuery = useMailboxesList();
    const threadsQuery = useThreadsList(undefined, {
        query: {
            enabled: !!selectedMailbox,
            refetchInterval: 30 * 1000, // 30 seconds
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

    const context = useMemo(() => ({
        mailboxes: mailboxQuery.data?.data ?? null,
        threads: threadsQuery.data?.data ?? null,
        messages: messagesQuery.data?.data ?? null,
        selectedMailbox,
        selectMailbox: setSelectedMailbox,
        selectedThread,
        selectThread: setSelectedThread,
        isPending: mailboxQuery.isPending || threadsQuery.isPending || messagesQuery.isPending,
        status: {
            mailboxes: mailboxQuery.status,
            threads: threadsQuery.status,
            messages: messagesQuery.status,
        },
        error: {
            mailboxes: mailboxQuery.error,
            threads: threadsQuery.error,
            messages: messagesQuery.error,
        }
    }), [
        mailboxQuery.data?.data,
        threadsQuery.data?.data,
        messagesQuery.data?.data,
        selectedMailbox,
        selectedThread,
    ]);

    useEffect(() => {
        const mailboxes = mailboxQuery.data?.data;
        if (mailboxes && mailboxes.length > 0) {
            setSelectedMailbox(mailboxes[0]);
        }
    }, [mailboxQuery.data?.data]);

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

    return <MailboxContext.Provider value={context}>{children}</MailboxContext.Provider>
};

export const useMailboxContext = () => {
    const context = useContext(MailboxContext);
    if (!context) {
        throw new Error("`useMailboxContext` must be used within a children of `MailboxProvider`.");
    }
    return context;
}
import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";
import { Mailbox, PaginatedThreadList, Thread, useMailboxesList, useThreadsList } from "../api/gen";

type MailboxContextType = {
    mailboxes: readonly Mailbox[] | null;
    threads: PaginatedThreadList | null;
    selectedMailbox: Mailbox | null;
    setSelectedMailbox: (mailbox: Mailbox) => void;
    isPending: boolean;
    status: {
        mailboxes: 'pending' | 'error' | 'success' | null,
        threads: 'pending' | 'error' | 'success' | null,
    };
    error: {
        mailboxes: unknown | null;
        threads: unknown | null;
    };
}

const MailboxContext = createContext<MailboxContextType>({
    mailboxes: null,
    threads: null,
    selectedMailbox: null,
    setSelectedMailbox: () => {},
    isPending: false,
    status: {
        mailboxes: null,
        threads: null,
    },
    error: {
        mailboxes: null,
        threads: null,
    },
});

/**
 * MailboxProvider is a context provider for the mailbox context.
 * It provides the mailboxes, threads and messages to its children
 * It also provides callbacks to select a mailbox, thread or message
 */
export const MailboxProvider = ({ children }: PropsWithChildren) => {
    const mailboxQuery = useMailboxesList();
    const [selectedMailbox, setSelectedMailbox] = useState<Mailbox | null>(null);
    const [selectedThread, setSelectedThread] = useState<Thread | null>(null);
    const threadsQuery = useThreadsList(undefined, {
        query: {
            enabled: !!selectedMailbox,
            refetchInterval: 30 * 1000,
        },
        request: {
            params: {
                mailbox_id: selectedMailbox?.id ?? '',
            }
        }
    });

    const context = useMemo(() => ({
        mailboxes: mailboxQuery.data?.data ?? null,
        threads: threadsQuery.data?.data ?? null,
        selectedMailbox,
        setSelectedMailbox,
        selectedThread,
        setSelectedThread,
        isPending: mailboxQuery.isPending || threadsQuery.isPending,
        status: {
            mailboxes: mailboxQuery.status,
            threads: threadsQuery.status,
        },
        error: {
            mailboxes: mailboxQuery.error,
            threads: threadsQuery.error,
        }
    }), [mailboxQuery.data?.data, threadsQuery.data?.data, selectedMailbox, selectedThread]);

    useEffect(() => {
        const mailboxes = mailboxQuery.data?.data;
        if (mailboxes && mailboxes.length > 0) {
            setSelectedMailbox(mailboxes[0]);
        }
    }, [mailboxQuery.data?.data]);

    return <MailboxContext.Provider value={context}>{children}</MailboxContext.Provider>
};

export const useMailboxContext = () => {
    const context = useContext(MailboxContext);
    if (!context) {
        throw new Error("`useMailboxContext` must be used within a children of `MailboxProvider`.");
    }
    return context;
}
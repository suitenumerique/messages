import { createContext, PropsWithChildren, useContext, useMemo, useState } from "react";
import { QueueMessage } from "./queued-message";
import { useMailboxContext } from "../mailbox";

type SentBoxContextType = {
    queuedMessages: readonly [string, boolean][];
    addQueuedMessage: (taskId: string, closeThread: boolean) => void;
    removeQueuedMessage: (taskId: string) => void;
}

const SentBoxContext = createContext<SentBoxContextType>({
    queuedMessages: [] as [string, boolean][],
    addQueuedMessage: () => {},
    removeQueuedMessage: () => {},
});

/**
 * SentBoxProvider is a provider to manage sending messages.
 * It manages a queue of sending messages and for each message, it displays a
 * toast to inform the user of the sending status.
 */
export const SentBoxProvider = ({ children }: PropsWithChildren) => {
    const { invalidateThreadsStats, invalidateThreadMessages, unselectThread } = useMailboxContext();
    const [queuedMessages, setQueuedMessages] = useState<[string, boolean][]>([]);

    const addQueuedMessage = (taskId: string, closeThread: boolean = false) => {
        setQueuedMessages(prev => [...prev, [taskId, closeThread]]);
    }

    const removeQueuedMessage = (taskId: string) => {
        setQueuedMessages(prev => prev.filter(([id]) => id !== taskId));
    }

    const handleSettled = (taskId: string, closeThread?: boolean) => {
        removeQueuedMessage(taskId);
        if (closeThread) unselectThread();
        invalidateThreadsStats();
        invalidateThreadMessages();
    }

    const context = useMemo(
        () => ({ queuedMessages, addQueuedMessage, removeQueuedMessage }),
        [queuedMessages, addQueuedMessage, removeQueuedMessage]
    );

    return (
        <SentBoxContext.Provider value={context}>
            {children}
            {
                queuedMessages.map(([taskId, closeThread]) => (
                    <QueueMessage
                        key={taskId}
                        taskId={taskId}
                        onSettled={() => { handleSettled(taskId, closeThread) }}
                    />
                ))
            }
        </SentBoxContext.Provider>
    )
}

export const useSentBox = () => {
    const context = useContext(SentBoxContext);
    if (!context) {
        throw new Error("`useSentbox` must be used within a children of `SentBoxProvider`.");
    }
    return context;
}

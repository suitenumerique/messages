import { createContext, PropsWithChildren, useContext, useMemo, useState } from "react";
import { QueueMessage } from "./queued-message";

type SentBoxContextType = {
    queuedMessages: readonly string[];
    addQueuedMessage: (taskId: string) => void;
    removeQueuedMessage: (taskId: string) => void;
}

const SentBoxContext = createContext<SentBoxContextType>({
    queuedMessages: [] as string[],
    addQueuedMessage: () => {},
    removeQueuedMessage: () => {},
});

/**
 * SentBoxProvider is a provider to manage sending messages.
 * It manages a queue of sending messages and for each message, it displays a
 * toast to inform the user of the sending status.
 */
export const SentBoxProvider = ({ children }: PropsWithChildren) => {
    const [queuedMessages, setQueuedMessages] = useState<string []>([]);

    const addQueuedMessage = (taskId: string) => {
        setQueuedMessages([...queuedMessages, taskId]);
    }

    const removeQueuedMessage = (taskId: string) => {
        setQueuedMessages(queuedMessages.filter(id => id !== taskId));
    }

    const context = useMemo(
        () => ({ queuedMessages, addQueuedMessage, removeQueuedMessage }),
        [queuedMessages, addQueuedMessage, removeQueuedMessage]
    );

    return (
        <SentBoxContext.Provider value={context}>
            {children}
            {
                queuedMessages.map(taskId => (
                    <QueueMessage
                        key={taskId}
                        taskId={taskId}
                        onSettled={() => removeQueuedMessage(taskId)}
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
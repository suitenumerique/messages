import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { MessageFormMode } from "@/features/forms/components/message-form";

type ThreadViewProviderProps = PropsWithChildren<{
    threadId: string;
    messageIds: readonly string[];
}>

/** Reply-form modes that can be requested from outside a message card. */
export type ReplyRequestMode = Exclude<MessageFormMode, "new">;

type ThreadViewContextType = {
    isReady: boolean;
    isMessageReady: (messageId: string) => boolean | undefined;
    setMessageReadiness: (messageId: string, isReady: boolean) => void;
    reset: (messageId?: string) => void;
    hasBeenInitialized: boolean;
    setHasBeenInitialized: (hasBeenInitialized: boolean) => void;
    isMessageFormFocused: boolean;
    setIsMessageFormFocused: (focused: boolean) => void;
    /** Incremented to ask the latest message to open its reply form. */
    replyRequest: number;
    /** Mode the latest message should open its reply form with. */
    replyRequestMode: ReplyRequestMode;
    requestReply: (mode?: ReplyRequestMode) => void;
}

const ThreadViewContext = createContext<ThreadViewContextType | undefined>(undefined);

/**
 * Provider to manage the thread view context.
 * It allows to track the readiness state of the thread view (Does all messages content are loaded?).
 */
const ThreadViewProvider = ({ threadId, messageIds, children }: ThreadViewProviderProps) => {
    const [messagesReadiness, setMessagesReadiness] = useState(new Map(messageIds.map((id) => [id, false])));
    const [hasBeenInitialized, setHasBeenInitialized] = useState(false);
    const [isMessageFormFocused, setIsMessageFormFocused] = useState(false);
    const [replyRequest, setReplyRequest] = useState(0);
    const [replyRequestMode, setReplyRequestMode] = useState<ReplyRequestMode>("reply");

    const requestReply = useCallback((mode: ReplyRequestMode = "reply") => {
        setReplyRequestMode(mode);
        setReplyRequest((n) => n + 1);
    }, []);

    const isReady = useMemo(() => {
        return Array.from(messagesReadiness.values()).every((isReady) => isReady === true);
    }, [messagesReadiness]);

    // Is a specific message ready?
    const isMessageReady = (messageId: string) => {
        return messagesReadiness.get(messageId);
    }

    // Update the readiness state of a specific message
    const setMessageReadiness = (messageId: string, isReady: boolean) => {
        if (messagesReadiness.has(messageId)) {
            setMessagesReadiness(prev => {
                const newMap = new Map(prev);
                newMap.set(messageId, isReady);
                return newMap;
            });
        } else {
            console.warn(`Message ${messageId} not registered in the readiness state context`);
        }
    }

    // Reset the readiness state of all messages or a specific message
    const reset = (messageId?: string) => {
        if (messageId) {
            setMessageReadiness(messageId, false);
        } else {
            setMessagesReadiness(new Map(messageIds.map((id) => [id, false])));
            setHasBeenInitialized(false);
        }
    }

    const context = useMemo(() => ({
        isReady,
        isMessageReady,
        setMessageReadiness,
        reset,
        hasBeenInitialized,
        setHasBeenInitialized,
        isMessageFormFocused,
        setIsMessageFormFocused,
        replyRequest,
        replyRequestMode,
        requestReply,
    }), [isReady, setMessageReadiness, isMessageReady, reset, hasBeenInitialized, setHasBeenInitialized, isMessageFormFocused, replyRequest, replyRequestMode, requestReply]);



    // Reset focus state when the active thread changes to prevent a stale
    // `true` from hiding ThreadEventInput on the next thread.
    useEffect(() => {
        setIsMessageFormFocused(false);
    }, [threadId]);

    // If the list of message IDs changes, update the readiness state context
    useEffect(() => {
        const currentMessageIds = Array.from(messagesReadiness.keys());
        const newMessageIds = messageIds.filter((id) => !currentMessageIds.includes(id));
        const removedMessageIds = currentMessageIds.filter((id) => !messageIds.includes(id));
        if (newMessageIds.length > 0 || removedMessageIds.length > 0) {
            const nextState = new Map(messagesReadiness);
            for (const messageId of newMessageIds) {
                nextState.set(messageId, false);
            }
            for (const messageId of removedMessageIds) {
                nextState.delete(messageId);
            }
            setMessagesReadiness(nextState);
        }

    }, [messageIds.join(',')]);

    return (
        <ThreadViewContext.Provider value={context}>
            {children}
        </ThreadViewContext.Provider>
    )
}

export const useThreadViewContext = () => {
    const context = useContext(ThreadViewContext);
    if (!context) {
        throw new Error("useThreadViewContext must be used within a ThreadViewProvider");
    }
    return context;
}

export default ThreadViewProvider;

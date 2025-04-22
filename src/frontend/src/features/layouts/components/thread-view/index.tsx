
import { useParams } from "next/navigation"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/mailbox/provider"
import { useEffect, useState } from "react"

export const ThreadView = () => {
    const params = useParams<{ mailboxId: string, threadId: string }>()
    const { threads, selectedThread, selectThread, messages } = useMailboxContext();
    const [showReplyAllForm, setShowReplyAllForm] = useState<boolean>(false);
    const latestMessage = messages?.results.reduce((acc, message) => {
        if (message.received_at > acc.received_at) {
            return message;
        }
        return acc;
    }, messages?.results[0]);

    useEffect(() => {
        if (selectedThread?.id !== params?.threadId) {
            const thread = threads?.results.find(({ id }) => id === params.threadId);
            if (thread) {
                selectThread(thread);
            }
        }
    }, [threads, params]);

    useEffect(() => {
        setShowReplyAllForm(false);
    }, [selectedThread]);

    if (!selectedThread) return null


    return (
        <div className="thread-view">
            <ActionBar handleReplyAll={() => setShowReplyAllForm(true)} />
            <div className="thread-view__messages">
                {messages?.results.map((message) => (
                    <ThreadMessage
                        key={message.id}
                        message={message}
                        showReplyAllForm={latestMessage?.id === message.id && showReplyAllForm}
                        resetReplyAllForm={() => setShowReplyAllForm(false)}
                    />
                ))}
            </div>
        </div>
    )
}
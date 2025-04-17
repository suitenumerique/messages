
import { useParams } from "next/navigation"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/mailbox/provider"
import { useEffect } from "react"

export const ThreadView = () => {
    const params = useParams<{ mailboxId: string, threadId: string }>()
    const { threads, selectedThread, selectThread, messages } = useMailboxContext();

    useEffect(() => {
        if (selectedThread?.id !== params?.threadId) {
            const thread = threads?.results.find(({ id }) => id === params.threadId);
            if (thread) {
                selectThread(thread);
            }
        }
    }, [threads, params]);

    if (!selectedThread) return null

    return (
        <div className="thread-view">
            <ActionBar />
            <div className="thread-view__messages">
                {messages?.results.map((message) => (
                    <ThreadMessage key={message.id} message={message} />
                ))}
            </div>
        </div>
    )
}
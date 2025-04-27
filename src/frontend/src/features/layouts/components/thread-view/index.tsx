import { useEffect, useRef } from "react"
import { useParams } from "next/navigation"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/mailbox/provider"
import useRead from "@/features/message/useRead"
import { useDebounceCallback } from "@/hooks/useDebounceCallback"

export const ThreadView = () => {
    const params = useParams<{ mailboxId: string, threadId: string }>()
    const toMarkAsReadQueue = useRef<string[]>([]);
    const debouncedMarkAsRead = useDebounceCallback(() => {
        if (toMarkAsReadQueue.current.length === 0) return;
        markAsRead({
            messageIds: toMarkAsReadQueue.current,
            onSuccess: () => {
                toMarkAsReadQueue.current = [];
            }   
        })
    }, 300);
    const { threads, selectedThread, selectThread, messages } = useMailboxContext();
    const rootRef = useRef<HTMLDivElement>(null);
    const { markAsRead } = useRead();
    const latestMessage = messages?.results.reduce((acc, message) => {
        if (message.received_at > acc.received_at) {
            return message;
        }
        return acc;
    }, messages?.results[0]);

    // Refs for all unread messages
    const unreadRefs = useRef<Record<string, HTMLElement | null>>({});
    // Find all unread message IDs
    const unreadMessageIds = messages?.results?.filter((m) => !m.read_at).map((m) => m.id) || [];

    useEffect(() => {
        if (selectedThread?.id !== params?.threadId) {
            const thread = threads?.results.find(({ id }) => id === params.threadId);
            if (thread) {
                selectThread(thread);
            }
        }
    }, [threads, params]);

    /**
     * Setup an intersection observer to mark messages as read when they are
     * scrolled into view.
     */
    useEffect(() => {
        if (!unreadMessageIds.length) return;

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const messageId = entry.target.getAttribute('data-message-id');
                const message = messages?.results.find(({ id }) => id === messageId);
                if (!message) return;
                if (entry.isIntersecting &&!message.read_at) {
                    toMarkAsReadQueue.current.push(messageId!);
                }
            });
            debouncedMarkAsRead();
        }, { threshold: 0.95, root: rootRef.current, rootMargin: "0px 40px 0px 0px" });

        unreadMessageIds.forEach(id => {
            const el = unreadRefs.current[id];
            if (el) {
                observer.observe(el);
            }
        });

        return () => {
            observer.disconnect();
        };
    }, [unreadMessageIds.join(","), messages]);

    if (!selectedThread) return null

    return (
        <div className="thread-view" ref={rootRef}>
            <ActionBar />
            <div className="thread-view__messages">
                {messages?.results.map((message) => {
                    const isLatest = latestMessage?.id === message.id;
                    const isUnread = !message.read_at;
                    return (
                    <ThreadMessage
                        key={message.id}
                        message={message}
                            isLatest={isLatest}
                            ref={isUnread ? (el => { unreadRefs.current[message.id] = el; }) : undefined}
                            data-message-id={message.id}
                    />
                    );
                })}
            </div>
        </div>
    )
}
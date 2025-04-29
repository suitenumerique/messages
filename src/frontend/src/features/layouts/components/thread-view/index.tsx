import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/router"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/mailbox/provider"
import useRead from "@/features/message/useRead"
import { useDebounceCallback } from "@/hooks/useDebounceCallback"
import usePrevious from "@/hooks/usePrevious"

export const ThreadView = () => {
    const router = useRouter();
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
    const previousSelectedThreadId = usePrevious(selectedThread?.id);
    const [canObserve, setCanObserve] = useState(false);
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
    // Find the message to scroll to on mount (the oldest unread message or the latest message)
    const messageToScrollTo = unreadMessageIds?.[0] || latestMessage?.id;

    useEffect(() => {
        const routerThreadId = router.query.threadId;
        if (selectedThread?.id !== routerThreadId) {
            const thread = threads?.results.find(({ id }) => id === routerThreadId);
            if (thread) selectThread(thread);
        }
    }, [threads, router.query.threadId]);

    useEffect(() => {
        if (previousSelectedThreadId === selectedThread?.id) return;
        // Delay the scroll to allow the iframe to be loaded...
        // TODO: Find a better way to do this.
        setTimeout(() => {
            const el = rootRef.current?.querySelector(`.thread-message[data-message-id="${messageToScrollTo}"]`);
            if (el) {
                el.scrollIntoView({ behavior: "instant", block: "end" });
                // Allow the observer to observe the messages after the scroll has completed
            }
            setCanObserve(true);
        }, 100);
    }, [selectedThread]);

    /**
     * Setup an intersection observer to mark messages as read when they are
     * scrolled into view.
     */
    useEffect(() => {
        if (!canObserve) return;

        const observer: IntersectionObserver = new IntersectionObserver((entries) => {
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

        if (!unreadMessageIds.length) return;
        unreadMessageIds.forEach(id => {
            const el = unreadRefs.current[id];
            if (el) {
                observer.observe(el);
            }
        });

        return () => {
            observer.disconnect();
        };
    }, [unreadMessageIds.join(","), messages, canObserve]);

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
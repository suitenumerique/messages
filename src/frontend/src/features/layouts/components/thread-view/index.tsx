import { useEffect, useMemo, useRef } from "react"
import { useParams, useSearchParams } from "next/navigation"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/mailbox/provider"
import useRead from "@/features/message/useRead"
import { useDebounceCallback } from "@/hooks/useDebounceCallback"
import { Message } from "@/features/api/gen/models"

type MessageWithDraftChild = Message & {
    draft_message?: Message;
}

export const ThreadView = () => {
    const params = useParams<{ mailboxId: string, threadId: string }>()
    const searchParams = useSearchParams();
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
    const filteredMessages = useMemo(() => {
        if (!messages?.results) return [];
        const isTrashView = searchParams.get('has_trashed') === '1';
        const isDraftView = searchParams.get('has_draft') === '1';
        if (isTrashView) return messages.results.filter((m) => !m.is_draft && m.is_trashed) as MessageWithDraftChild[];
        const undraftMessages = messages.results.filter((m) => (
            (isDraftView && m.is_draft && !m.is_trashed && !m.parent_id) ||
            !m.is_draft && !m.is_trashed
        )) as MessageWithDraftChild[];
        const draftMessages = messages.results.filter((m) => m.is_draft && m.parent_id && !m.is_trashed);
        draftMessages.forEach((m) => {
            const parentMessage = undraftMessages.find((um) => um.id === m.parent_id);
            if (parentMessage) {
                parentMessage.draft_message = m;
            }
        });
        return undraftMessages;
    }, [messages]);

    const latestMessage = filteredMessages?.reduce((acc, message) => {
        if (message!.sent_at && acc!.sent_at && message!.sent_at > acc!.sent_at) {
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
            <div className="thread-view__messages-list">
                {filteredMessages.map((message) => {
                    const isLatest = latestMessage?.id === message.id;
                    const isUnread = message.is_unread;
                    return (
                        <ThreadMessage
                            key={message.id}
                            message={message}
                            isLatest={isLatest}
                            ref={isUnread ? (el => { unreadRefs.current[message.id] = el; }) : undefined}
                            data-message-id={message.id}
                            draftMessage={message?.draft_message}
                        />
                    );
                })}
            </div>
        </div>
    )
}
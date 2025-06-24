import { useEffect, useMemo, useRef, useState } from "react"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/providers/mailbox"
import useRead from "@/features/message/use-read"
import { useDebounceCallback } from "@/hooks/use-debounce-callback"
import { Message } from "@/features/api/gen/models"
import { Spinner } from "@gouvfr-lasuite/ui-kit"
import { useSearchParams } from "next/navigation"
import { Banner } from "@/features/ui/components/banner"
import { Button } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next"
import { ThreadViewLabelsList } from "./components/thread-view-labels-list"

type MessageWithDraftChild = Message & {
    draft_message?: Message;
}

export const ThreadView = () => {
    const { t } = useTranslation();
    const searchParams = useSearchParams();
    const toMarkAsReadQueue = useRef<string[]>([]);
    const isTrashView = searchParams.get('has_trashed') === '1';
    const [showTrashedMessages, setShowTrashedMessages] = useState(isTrashView);
    const debouncedMarkAsRead = useDebounceCallback(() => {
        if (toMarkAsReadQueue.current.length === 0) return;
        markAsRead({
            messageIds: toMarkAsReadQueue.current,
            onSuccess: () => {
                toMarkAsReadQueue.current = [];
            }
        })
    }, 300);
    const { selectedThread, messages, queryStates } = useMailboxContext();
    const rootRef = useRef<HTMLDivElement>(null);
    const { markAsRead } = useRead();
    // Nest draft messages under their parent messages
    const messagesWithDraftChildren = useMemo(() => {
        if (!messages?.results) return [];
        const rootMessages: MessageWithDraftChild[] = messages.results.filter((m) =>  !m.is_draft || !m.parent_id);
        const draftChildren  = messages.results.filter((m) => m.is_draft && m.parent_id);
        draftChildren.forEach((m) => {
            const parentMessage = rootMessages.find((um) => um.id === m.parent_id);
            if (parentMessage) {
                parentMessage.draft_message = m;
            }
        });
        return rootMessages
    }, [messages]);

    /**
     * If we are in the trash view, we only want to show trashed messages
     * otherwise, we want to show only non-trashed messages
     *
     * If we are in the trash view and the user has clicked on the "show trashed messages" button,
     * we want to show all messages.
     */
    const filteredMessages = useMemo(() => {
        if(!isTrashView && showTrashedMessages) return messagesWithDraftChildren;
        return messagesWithDraftChildren.filter((m) => m.is_trashed === isTrashView);
    }, [messagesWithDraftChildren, isTrashView, showTrashedMessages]);

    const latestMessage = messagesWithDraftChildren.filter((m) => m.is_trashed === isTrashView).reduce((acc, message) => {
        if (message!.sent_at && acc!.sent_at && message!.sent_at > acc!.sent_at) {
            return message;
        }
        return acc;
    }, filteredMessages[0]);

    // Refs for all unread messages
    const unreadRefs = useRef<Record<string, HTMLElement | null>>({});
    // Find all unread message IDs
    const unreadMessageIds = messages?.results?.filter((m) => !m.read_at).map((m) => m.id) || [];
    const trashedMessageIds = messages?.results?.filter((m) => m.is_trashed).map((m) => m.id) || [];
    const isThreadTrashed = trashedMessageIds.length === messages?.results?.length;

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

    useEffect(() => () => {
        setShowTrashedMessages(isTrashView);
    }, [selectedThread]);

    if (!selectedThread) return null

    if (queryStates.messages.isLoading) return (
        <div className="thread-view thread-view--loading">
            <Spinner />
        </div>
    )

    return (
        <div className="thread-view" ref={rootRef}>
            <ActionBar canUndelete={isThreadTrashed} />
            <div className="thread-view__messages-list">
                {
                    selectedThread!.labels.length > 0 && (
                        <ThreadViewLabelsList labels={selectedThread!.labels} />
                    )
                }
                {filteredMessages!.map((message) => {
                    const isLatest = latestMessage?.id === message.id;
                    const isUnread = message.is_unread;
                    return (
                        <ThreadMessage
                            key={message.id}
                            message={message}
                            isLatest={isLatest}
                            ref={isUnread ? (el => { unreadRefs.current[message.id] = el; }) : undefined}
                            data-message-id={message.id}
                            draftMessage={message.draft_message}
                        />
                    );
                })}
                {trashedMessageIds.length > 0 && !showTrashedMessages && (
                    <Banner icon={<span className="material-icons">delete</span>} type="info">
                        <div className="thread-view__trashed-banner__content">
                            <p>{t('thread-view.trashed-banner.message', { count: trashedMessageIds.length })}</p>
                            <div className="thread-view__trashed-banner__actions">
                                <Button
                                    onClick={() => setShowTrashedMessages(!showTrashedMessages)}
                                    color="primary-text"
                                    size="small"
                                    icon={<span className="material-icons">visibility</span>}
                                >
                                    {t('actions.show')}
                                </Button>
                            </div>
                        </div>
                    </Banner>
                )}
            </div>
        </div>
    )
}

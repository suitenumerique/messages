import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { ThreadActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { ThreadEvent } from "./components/thread-event"
import { ThreadEventInput } from "./components/thread-event-input"
import { useMailboxContext, TimelineItem, isThreadEvent } from "@/features/providers/mailbox"
import useRead from "@/features/message/use-read"
import { useDebounceCallback } from "@/hooks/use-debounce-callback"
import { Message, Thread, ThreadAccessRoleChoices, ThreadEvent as ThreadEventModel } from "@/features/api/gen/models"
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit"
import { Banner } from "@/features/ui/components/banner"
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link"
import { useTranslation } from "react-i18next"
import { ThreadViewLabelsList } from "./components/thread-view-labels-list"
import { ThreadSummary } from "./components/thread-summary";
import clsx from "clsx";
import ThreadViewProvider, { useThreadViewContext } from "./provider";
import useSpam from "@/features/message/use-spam";
import ViewHelper from "@/features/utils/view-helper";

type MessageWithDraftChild = Message & {
    draft_message?: Message;
}

type ThreadViewComponentProps = {
    threadItems: readonly TimelineItem[],
    mailboxId: string,
    thread: Thread,
    showTrashedMessages: boolean,
    setShowTrashedMessages: (show: boolean) => void,
    stats: { trashed: number, archived: number, total: number },
    showIMInput: boolean,
}

const ThreadViewComponent = ({ threadItems, mailboxId, thread, showTrashedMessages, setShowTrashedMessages, stats, showIMInput }: ThreadViewComponentProps) => {
    const { t } = useTranslation();
    const latestSeenDate = useRef<string | null>(null);
    const stickyContainerRef = useRef<HTMLDivElement>(null);
    const { markAsReadAt } = useRead();
    const [editingEvent, setEditingEvent] = useState<ThreadEventModel | null>(null);
    const { markAsNotSpam } = useSpam();
    const debouncedMarkAsRead = useDebounceCallback((threadId: string, readAt: string) => {
        markAsReadAt({ threadIds: [threadId], readAt });
    }, 150);

    const rootRef = useRef<HTMLDivElement>(null);
    const isAISummaryEnabled = useFeatureFlag(FEATURE_KEYS.AI_SUMMARY);
    const { isReady, reset, hasBeenInitialized, setHasBeenInitialized } = useThreadViewContext();
    // Refs for all unread messages
    const unreadRefs = useRef<Record<string, HTMLElement | null>>({});
    // Find all unread message IDs
    const messages = useMemo(() => threadItems.filter(item => item.type === 'message').map(item => item.data as MessageWithDraftChild), [threadItems]);
    const unreadMessageIds = useMemo(() => messages.filter((m) => m.is_unread).map((m) => m.id), [messages]);
    const draftMessageIds = useMemo(() => messages.filter((m) => m.draft_message).map((m) => m.id), [messages]);
    const isThreadTrashed = stats.trashed === stats.total;
    const isThreadArchived = stats.archived === stats.total;
    const isThreadSender = messages?.some((m) => m.is_sender);
    const latestMessage = messages.reduce((acc, message) => {
        if (message!.created_at && acc!.created_at && message!.created_at > acc!.created_at) {
            return message;
        }
        return acc;
    }, messages[0]);

    /**
     * Scroll to the bottom of the thread view.
     */
    const scrollToBottom = useCallback(() => {
        requestAnimationFrame(() => {
            rootRef.current?.scrollTo({
                top: rootRef.current.scrollHeight,
                behavior: 'smooth',
            });
        });
    }, []);

    /**
     * Setup an intersection observer to mark messages as read when they are
     * scrolled into view.
     */
    useEffect(() => {
        if (!unreadMessageIds.length || !isReady) return;

        const stickyContainerHeight = stickyContainerRef.current?.getBoundingClientRect().height || 125;
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;

                const createdAt = entry.target.getAttribute('data-created-at');
                if (!createdAt) return;


                // Track the most recent message scrolled into view
                if (!latestSeenDate.current || new Date(createdAt) > new Date(latestSeenDate.current)) {
                    latestSeenDate.current = createdAt;
                }
                debouncedMarkAsRead(thread.id, latestSeenDate.current);
            });

        }, { root: rootRef.current, rootMargin: `-${stickyContainerHeight}px 0px 0px 0px` });

        unreadMessageIds.forEach(messageId => {
            const el = unreadRefs.current[messageId];
            if (el) {
                observer.observe(el);
            }
        });

        return () => {
            observer.disconnect();
        };
    }, [isReady, unreadMessageIds.join(","), thread.id]);

    useEffect(() => {
        if (isReady && !hasBeenInitialized) {
            let messageToScroll = latestMessage?.id;
            let selector = `#thread-message-${messageToScroll}`;
            if (draftMessageIds.length > 0) {
                messageToScroll = draftMessageIds[0];
                selector = `#thread-message-${messageToScroll} > .thread-message__reply-form`;
            } else if (unreadMessageIds.length > 0) {
                messageToScroll = unreadMessageIds[0];
                selector = `#thread-message-${messageToScroll}`;
            }

            const el = document.querySelector<HTMLElement>(selector);
            if (el) {
                rootRef.current?.scrollTo({ top: el.offsetTop - 225, behavior: 'instant' });
                setHasBeenInitialized(true);
            }
        }
    }, [isReady]);

    const handleEventDelete = useCallback((eventId: string) => {
        if (editingEvent?.id === eventId) {
            setEditingEvent(null);
        }
    }, [editingEvent]);

    useEffect(() => () => {
        reset();
        setEditingEvent(null);
    }, [thread.id]);

    return (
        <div id={SKIP_LINK_TARGET_ID} className={clsx("thread-view", { "thread-view--talk": isThreadSender })} ref={rootRef}>
            <div className="thread-view__sticky-container" ref={stickyContainerRef}>
                <header className="thread-view__header">
                    <div className="thread-view__header__top">
                        <ThreadActionBar canUndelete={isThreadTrashed} canUnarchive={isThreadArchived} />
                        <h2 className="thread-view__subject">
                            {thread.has_starred &&
                                <Icon name="star" type={IconType.FILLED} className="thread-view__subject__star" aria-label={t('Starred')} />
                            }
                            {thread.subject || t('No subject')}
                        </h2>
                    </div>
                </header>
            </div>
            {
                thread.labels.length > 0 && (
                    <ThreadViewLabelsList labels={thread.labels} />
                )
            }
            {isAISummaryEnabled && (
                <ThreadSummary
                    threadId={thread.id}
                    summary={thread.summary}
                    selectedMailboxId={mailboxId}
                    selectedThread={thread}
                />
            )}
            <div className="thread-view__messages-list">
                {thread.is_spam && (
                    <Banner
                        icon={<Icon name="report" type={IconType.OUTLINED} />}
                        type="warning"
                        actions={[{ label: t('Remove report'), onClick: () => markAsNotSpam({ threadIds: [thread.id] }) }]}
                    >
                        <p>{t('This thread has been reported as spam.')}</p>
                    </Banner>
                )}
                {stats.trashed > 0 && !showTrashedMessages && (
                    <Banner
                        icon={<Icon name="delete" type={IconType.OUTLINED} />}
                        type="info" actions={[{
                            label: t('Show'),
                            onClick: () => setShowTrashedMessages(!showTrashedMessages)
                        }]}
                    >
                        {t(
                            '{{count}} messages of this thread have been deleted.',
                            {
                                count: stats.trashed,
                                defaultValue_one: "{{count}} message of this thread has been deleted.",
                            }
                        )}
                    </Banner>
                )}
                {threadItems.map((item, index) => {
                    if (isThreadEvent(item)) {
                        const prevItem = index > 0 ? threadItems[index - 1] : null;
                        const prevEvent = isThreadEvent(prevItem) ? prevItem.data : null;
                        return <ThreadEvent key={`event-${item.data.id}`} event={item.data} previousEvent={prevEvent} onEdit={setEditingEvent} onDelete={handleEventDelete} />;
                    }
                    const message = item.data as MessageWithDraftChild;
                    const isLatest = latestMessage?.id === message.id;
                    const isUnread = message.is_unread;
                    return (
                        <ThreadMessage
                            key={message.id}
                            message={message}
                            isLatest={isLatest}
                            ref={isUnread ? (el => { unreadRefs.current[message.id] = el; }) : undefined}
                            data-message-id={message.id}
                            data-created-at={message.created_at}
                            draftMessage={message.draft_message}
                        />
                    );
                })}
            </div>
            {showIMInput && (
                <ThreadEventInput
                    threadId={thread.id}
                    editingEvent={editingEvent}
                    onCancelEdit={() => setEditingEvent(null)}
                    onEventCreated={scrollToBottom}
                />
            )}
        </div>
    )
}

export const ThreadView = () => {
    const isTrashView = ViewHelper.isTrashedView();
    const { selectedMailbox, selectedThread, messages, threadItems, queryStates } = useMailboxContext();
    const [showTrashedMessages, setShowTrashedMessages] = useState(isTrashView);
    // Nest draft messages under their parent messages
    const messagesWithDraftChildren = useMemo(() => {
        if (!messages) return [];
        const rootMessages: MessageWithDraftChild[] = messages.filter((m) => !m.is_draft || !m.parent_id);
        const draftChildren = messages.filter((m) => m.is_draft && m.parent_id);
        draftChildren.forEach((m) => {
            const parentMessage = rootMessages.find((um) => um.id === m.parent_id);
            if (parentMessage) {
                parentMessage.draft_message = m;
            }
        });
        return rootMessages
    }, [messages]);
    const messagesStats = useMemo(() => ({
        trashed: messagesWithDraftChildren?.filter((m) => m.is_trashed).length || 0,
        archived: messagesWithDraftChildren?.filter((m) => m.is_archived).length || 0,
        total: messagesWithDraftChildren?.length || 0,
    }), [messagesWithDraftChildren]);
    // Show IM input for shared mailboxes, or when the current mailbox
    // has editor access on a thread shared with other mailboxes.
    const hasEditorAccess = selectedThread?.accesses?.some(
        access => access.mailbox.id === selectedMailbox?.id
            && access.role === ThreadAccessRoleChoices.editor
    );
    const hasMultipleAccesses = (selectedThread?.accesses?.length ?? 0) > 1;
    const isSharedMailbox = selectedMailbox?.is_identity === false;
    const showIMInput = Boolean((isSharedMailbox || hasMultipleAccesses) && hasEditorAccess);

    // Build filtered timeline items: enrich messages with draft children,
    // apply trash filtering, and keep all events.
    const filteredThreadItems = useMemo(() => {
        if (!threadItems) return [];
        const messagesById = new Map(messagesWithDraftChildren.map((m) => [m.id, m]));
        const showAll = !isTrashView && showTrashedMessages;
        return threadItems.flatMap<TimelineItem>((item) => {
            if (item.type === 'event') return [item];
            const message = messagesById.get(item.data.id);
            if (!message) return [];
            if (!showAll && message.is_trashed !== isTrashView) return [];
            return [{ type: 'message', data: message, created_at: item.created_at }];
        });
    }, [threadItems, messagesWithDraftChildren, isTrashView, showTrashedMessages]);

    const messageIds = filteredThreadItems
        .filter((item): item is Extract<TimelineItem, { type: 'message' }> => item.type === 'message')
        .map(item => item.data.id);

    useEffect(() => () => {
        setShowTrashedMessages(isTrashView);
    }, [selectedThread]);

    if (!selectedMailbox || !selectedThread) return null

    if (queryStates.messages.isLoading || queryStates.threadEvents.isLoading) {
        return (
            <div className="thread-view thread-view--loading">
                <Spinner />
            </div>
        )
    }

    return (
        <ThreadViewProvider messageIds={messageIds}>
            <ThreadViewComponent
                mailboxId={selectedMailbox!.id}
                thread={selectedThread!}
                threadItems={filteredThreadItems}
                showTrashedMessages={showTrashedMessages}
                setShowTrashedMessages={setShowTrashedMessages}
                stats={messagesStats}
                showIMInput={showIMInput}
            />
        </ThreadViewProvider>
    )
}

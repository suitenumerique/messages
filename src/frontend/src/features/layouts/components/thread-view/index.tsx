import { useEffect, useMemo, useRef, useState } from "react"
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { ThreadActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/providers/mailbox"
import useRead from "@/features/message/use-read"
import { useDebounceCallback } from "@/hooks/use-debounce-callback"
import { Message, Thread } from "@/features/api/gen/models"
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
    messages: readonly MessageWithDraftChild[],
    mailboxId: string,
    thread: Thread,
    showTrashedMessages: boolean,
    setShowTrashedMessages: (show: boolean) => void,
    stats: { trashed: number, archived: number, total: number },
}

const ThreadViewComponent = ({ messages, mailboxId, thread, showTrashedMessages, setShowTrashedMessages, stats }: ThreadViewComponentProps) => {
    const { t } = useTranslation();
    const toMarkAsReadQueue = useRef<string[]>([]);
    const stickyContainerRef = useRef<HTMLDivElement>(null);
    const { markAsRead } = useRead();
    const { markAsNotSpam } = useSpam();
    const debouncedMarkAsRead = useDebounceCallback(() => {
        if (toMarkAsReadQueue.current.length === 0) return;
        markAsRead({ messageIds: toMarkAsReadQueue.current });
        toMarkAsReadQueue.current = [];
    }, 150);

    const rootRef = useRef<HTMLDivElement>(null);
    const isAISummaryEnabled = useFeatureFlag(FEATURE_KEYS.AI_SUMMARY);
    const { isReady, reset, hasBeenInitialized, setHasBeenInitialized } = useThreadViewContext();
    // Refs for all unread messages
    const unreadRefs = useRef<Record<string, HTMLElement | null>>({});
    // Find all unread message IDs
    const unreadMessageIds = messages.filter((m) => m.is_unread).map((m) => m.id) || [];
    const draftMessageIds = messages.filter((m) => m.draft_message).map((m) => m.id) || [];
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
     * Setup an intersection observer to mark messages as read when they are
     * scrolled into view.
     */
    useEffect(() => {
        if (!unreadMessageIds.length || !isReady) return;

        const stickyContainerHeight = stickyContainerRef.current?.getBoundingClientRect().height || 125;
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const messageId = entry.target.getAttribute('data-message-id');
                const message = messages.find(({ id }) => id === messageId);
                if (!message) return;

                if (entry.isIntersecting && message.is_unread && toMarkAsReadQueue.current.indexOf(messageId!) === -1) {
                    toMarkAsReadQueue.current.push(messageId!);
                    debouncedMarkAsRead();
                }
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

    useEffect(() => () => {
        reset();
    }, [thread.id]);

    return (
        <div id={SKIP_LINK_TARGET_ID} className={clsx("thread-view", { "thread-view--talk": isThreadSender })} ref={rootRef}>
            <div className="thread-view__sticky-container" ref={stickyContainerRef}>
                <header className="thread-view__header">
                    <div className="thread-view__header__top">
                        <ThreadActionBar canUndelete={isThreadTrashed} canUnarchive={isThreadArchived} />
                        <h2 className="thread-view__subject">{thread.subject || t('No subject')}</h2>
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
                        type="info" actions={[{ label: t('Show'),
                        onClick: () => setShowTrashedMessages(!showTrashedMessages) }]}
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
                {messages.map((message) => {
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
            </div>
        </div>
    )
}

export const ThreadView = () => {
    const isTrashView = ViewHelper.isTrashedView();
    const { selectedMailbox, selectedThread, messages, queryStates } = useMailboxContext();
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
    /**
     * If we are in the trash view, we only want to show trashed messages
     * otherwise, we want to show only non-trashed messages
     *
     * If we are not in the trash view and the user has clicked on the "show trashed messages" button,
     * we want to show all messages.
     */
    const filteredMessages = useMemo(() => {
        if (!isTrashView && showTrashedMessages) return messagesWithDraftChildren;
        return messagesWithDraftChildren.filter((m) => m.is_trashed === isTrashView);
    }, [messagesWithDraftChildren, isTrashView, showTrashedMessages]);

    useEffect(() => () => {
        setShowTrashedMessages(isTrashView);
    }, [selectedThread]);

    if (!selectedMailbox || !selectedThread) return null

    if (queryStates.messages.isLoading) {
        return (
            <div className="thread-view thread-view--loading">
                <Spinner />
            </div>
        )
    }

    return (
        <ThreadViewProvider messageIds={filteredMessages.map((m) => m.id) || []}>
            <ThreadViewComponent
                mailboxId={selectedMailbox!.id}
                thread={selectedThread!}
                messages={filteredMessages}
                showTrashedMessages={showTrashedMessages}
                setShowTrashedMessages={setShowTrashedMessages}
                stats={messagesStats}
            />
        </ThreadViewProvider>
    )
}

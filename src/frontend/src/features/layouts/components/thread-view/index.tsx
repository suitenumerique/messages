import { useEffect, useMemo, useRef, useState } from "react"
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { useMailboxContext } from "@/features/providers/mailbox"
import useRead from "@/features/message/use-read"
import { useDebounceCallback } from "@/hooks/use-debounce-callback"
import { Message, Thread } from "@/features/api/gen/models"
import { Spinner } from "@gouvfr-lasuite/ui-kit"
import { useSearchParams } from "next/navigation"
import { Banner } from "@/features/ui/components/banner"
import { Button } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next"
import { ThreadViewLabelsList } from "./components/thread-view-labels-list"
import { ThreadSummary } from "./components/thread-summary";
import clsx from "clsx";
import ThreadViewProvider, { useThreadViewContext } from "./provider";

type MessageWithDraftChild = Message & {
    draft_message?: Message;
}

type ThreadViewComponentProps = {
    messages: readonly MessageWithDraftChild[],
    mailboxId: string,
    thread: Thread,
    showTrashedMessages: boolean,
    setShowTrashedMessages: (show: boolean) => void,
    searchParams: URLSearchParams,
}

const ThreadViewComponent = ({ messages, mailboxId, thread, showTrashedMessages, setShowTrashedMessages, searchParams }: ThreadViewComponentProps) => {
    const { t } = useTranslation();
    const toMarkAsReadQueue = useRef<string[]>([]);
    const stickyContainerRef = useRef<HTMLDivElement>(null);
    const debouncedMarkAsRead = useDebounceCallback(() => {
        if (toMarkAsReadQueue.current.length === 0) return;
        markAsRead({ messageIds: toMarkAsReadQueue.current });
        toMarkAsReadQueue.current = [];
    }, 150);

    const rootRef = useRef<HTMLDivElement>(null);
    const { markAsRead } = useRead();
    const isAISummaryEnabled = useFeatureFlag(FEATURE_KEYS.AI_SUMMARY);
    const { isReady, reset } = useThreadViewContext();
    // Refs for all unread messages
    const unreadRefs = useRef<Record<string, HTMLElement | null>>({});
    // Find all unread message IDs
    const unreadMessageIds = messages.filter((m) => m.is_unread).map((m) => m.id) || [];
    const draftMessageIds = messages.filter((m) => m.draft_message).map((m) => m.id) || [];
    const trashedMessageIds = messages.filter((m) => m.is_trashed).map((m) => m.id) || [];
    const archivedMessageIds = messages.filter((m) => m.is_archived).map((m) => m.id) || [];
    const isThreadTrashed = trashedMessageIds.length === messages.length;
    const isThreadArchived = archivedMessageIds.length === messages.length;
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
        if (isReady) {
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
            }
        }
    }, [isReady]);

    useEffect(() => {
        reset();
    }, [thread.id]);


    return (
        <div className={clsx("thread-view", { "thread-view--talk": isThreadSender })} ref={rootRef}>
            <div className="thread-view__sticky-container" ref={stickyContainerRef}>
                <ActionBar canUndelete={isThreadTrashed} canUnarchive={isThreadArchived} />
                <header className="thread-view__header">
                    <h2 className="thread-view__subject">{thread.subject || t('No subject')}</h2>
                    {
                        thread.labels.length > 0 && (
                            <ThreadViewLabelsList labels={thread.labels} />
                        )
                    }
                </header>
            </div>
            {isAISummaryEnabled && (
                <ThreadSummary
                    threadId={thread.id}
                    summary={thread.summary}
                    selectedMailboxId={mailboxId}
                    searchParams={searchParams}
                    selectedThread={thread}
                />
            )}
            <div className="thread-view__messages-list">
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
                {trashedMessageIds.length > 0 && !showTrashedMessages && (
                    <Banner icon={<span className="material-icons">delete</span>} type="info">
                        <div className="thread-view__trashed-banner__content">
                            <p>
                                {t(
                                    '{{count}} messages of this thread have been deleted.',
                                    {
                                        count: trashedMessageIds.length,
                                        defaultValue_one: "{{count}} message of this thread has been deleted.",
                                    }
                                )}
                            </p>
                            <div className="thread-view__trashed-banner__actions">
                                <Button
                                    onClick={() => setShowTrashedMessages(!showTrashedMessages)}
                                    color="primary-text"
                                    size="small"
                                    icon={<span className="material-icons">visibility</span>}
                                >
                                    {t('Show')}
                                </Button>
                            </div>
                        </div>
                    </Banner>
                )}
            </div>
        </div>
    )
}

export const ThreadView = () => {
    const searchParams = useSearchParams();
    const isTrashView = searchParams.get('has_trashed') === '1';
    const { selectedMailbox, selectedThread, messages, queryStates } = useMailboxContext();
    const [showTrashedMessages, setShowTrashedMessages] = useState(isTrashView);
    // Nest draft messages under their parent messages
    const messagesWithDraftChildren = useMemo(() => {
        if (!messages?.results) return [];
        const rootMessages: MessageWithDraftChild[] = messages.results.filter((m) => !m.is_draft || !m.parent_id);
        const draftChildren = messages.results.filter((m) => m.is_draft && m.parent_id);
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
                searchParams={searchParams}
            />
        </ThreadViewProvider>
    )
}

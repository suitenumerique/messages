import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { ThreadActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"
import { ThreadEvent, isCondensed } from "./components/thread-event"
import { ThreadEventInput } from "./components/thread-event-input"
import { useMailboxContext, TimelineItem, isThreadEvent } from "@/features/providers/mailbox"
import useRead from "@/features/message/use-read"
import useMentionRead from "@/features/message/use-mention-read"
import { useDebounceCallback } from "@/hooks/use-debounce-callback"
import { useVisibilityObserver } from "@/hooks/use-visibility-observer"
import { MailboxRoleChoices, Message, Thread, ThreadEvent as ThreadEventModel } from "@/features/api/gen/models"
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

/**
 * Fallback height (px) used when measuring the sticky header before the
 * DOM ref is populated. Matches the rendered header height closely enough
 * for the IntersectionObserver to ignore content hidden behind it.
 */
const STICKY_HEADER_FALLBACK_HEIGHT = 125;

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
    // Refs for thread events with unread mentions
    const mentionRefs = useRef<Record<string, HTMLElement | null>>({});
    const { markMentionsRead } = useMentionRead(thread.id);
    // Find all unread message IDs
    const messages = useMemo(() => threadItems.filter(item => item.type === 'message').map(item => item.data as MessageWithDraftChild), [threadItems]);
    const unreadMessageIds = useMemo(() => messages.filter((m) => m.is_unread).map((m) => m.id), [messages]);
    const draftMessageIds = useMemo(() => messages.filter((m) => m.draft_message).map((m) => m.id), [messages]);
    const unreadMentionEventIds = useMemo(() =>
        threadItems
            .filter((item): item is Extract<typeof item, { type: 'event' }> =>
                item.type === 'event' && (item.data as ThreadEventModel).has_unread_mention === true
            )
            .map(item => item.data.id),
        [threadItems]
    );
    /**
     * Walks the timeline once to build a map <rootId, true> for
     * condensed-IM groups that contain at least one unread mention.
     *
     * A "root" is an IM event whose header is rendered (first event
     * of a condensed run). Surfacing the unread-mention badge on the root
     * means that a mention on a condensed sibling — whose own header is
     * hidden — still draws the user's eye via the root's header.
     */
    const unreadMentionGroupMap = useMemo(() => {
        const map = new Map<string, boolean>();
        let currentRootId: string | null = null;
        let prevEvent: ThreadEventModel | null = null;
        for (const item of threadItems) {
            if (!isThreadEvent(item)) {
                currentRootId = null;
                prevEvent = null;
                continue;
            }
            const current = item.data as ThreadEventModel;
            if (!isCondensed(current, prevEvent)) {
                currentRootId = current.id;
            }
            if (current.has_unread_mention && currentRootId) {
                map.set(currentRootId, true);
            }
            prevEvent = current;
        }
        return map;
    }, [threadItems]);

    // Mention IDs accumulated across debounce windows. The intersection
    // observer can fire several times within the 150ms window; using a ref
    // (instead of a value passed to the debounced callback) preserves earlier
    // batches that would otherwise be overwritten by the trailing-edge debounce.
    const pendingMentionIdsRef = useRef<Set<string>>(new Set());
    // Mentions already PATCHed during this thread session. `useMentionRead`
    // intentionally does not update the thread events cache (to keep the
    // "Mentioned" badge visible for the whole session), so an event stays in
    // `unreadMentionEventIds` until the next natural refetch. Without this
    // guard, scrolling back onto a flagged event would re-enqueue it on every
    // visibility pass and trigger redundant PATCH + stats invalidations.
    // Cleared on thread switch via the cleanup effect below.
    const sentMentionIdsRef = useRef<Set<string>>(new Set());
    const flushPendingMentions = useCallback(() => {
        if (pendingMentionIdsRef.current.size === 0) return;
        const ids = [...pendingMentionIdsRef.current];
        pendingMentionIdsRef.current.clear();
        markMentionsRead(ids);
    }, [markMentionsRead]);
    const debouncedFlushMentions = useDebounceCallback(flushPendingMentions, 150);
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
     * Mark messages as read once they scroll into view. Tracks the latest
     * timestamp seen so a single debounced call covers all messages above it.
     */
    const topOffset = stickyContainerRef.current?.getBoundingClientRect().height || STICKY_HEADER_FALLBACK_HEIGHT;
    useVisibilityObserver({
        enabled: isReady,
        ids: unreadMessageIds,
        refs: unreadRefs,
        rootRef,
        topOffset,
        onVisible: (entry) => {
            const createdAt = entry.target.getAttribute('data-created-at');
            if (!createdAt) return;
            // Track the most recent message scrolled into view
            if (!latestSeenDate.current || new Date(createdAt) > new Date(latestSeenDate.current)) {
                latestSeenDate.current = createdAt;
            }
            debouncedMarkAsRead(thread.id, latestSeenDate.current);
        },
    });

    /**
     * Mark mentions as read when their ThreadEvent scrolls into view.
     * IDs are accumulated through `pendingMentionIdsRef` so several batches
     * within the same debounce window are flushed together.
     */
    useVisibilityObserver({
        enabled: isReady,
        ids: unreadMentionEventIds,
        refs: mentionRefs,
        rootRef,
        topOffset,
        onVisible: (entry) => {
            const eventId = entry.target.getAttribute('data-event-id');
            if (!eventId) return;
            if (sentMentionIdsRef.current.has(eventId)) return;
            sentMentionIdsRef.current.add(eventId);
            pendingMentionIdsRef.current.add(eventId);
            debouncedFlushMentions();
        },
    });

    useEffect(() => {
        if (isReady && !hasBeenInitialized) {
            let selector = `#thread-message-${latestMessage?.id}`;
            if (draftMessageIds.length > 0) {
                // Drafts take precedence: jump straight to the reply form.
                selector = `#thread-message-${draftMessageIds[0]} > .thread-message__reply-form`;
            } else {
                // Otherwise, scroll to the earliest unread item in chronological
                // order — either an unread message or a ThreadEvent (IM) carrying
                // an unread mention of the current user.
                const firstUnreadItem = threadItems.find((item) => {
                    if (item.type === 'message') {
                        return (item.data as MessageWithDraftChild).is_unread;
                    }
                    return (item.data as ThreadEventModel).has_unread_mention === true;
                });
                if (firstUnreadItem) {
                    selector = firstUnreadItem.type === 'message'
                        ? `#thread-message-${firstUnreadItem.data.id}`
                        : `#thread-event-${firstUnreadItem.data.id}`;
                }
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
        pendingMentionIdsRef.current.clear();
        sentMentionIdsRef.current.clear();
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
                        const eventData = item.data as ThreadEventModel;
                        return (
                            <ThreadEvent
                                key={`event-${item.data.id}`}
                                event={item.data}
                                onEdit={setEditingEvent}
                                onDelete={handleEventDelete}
                                isCondensed={isCondensed(eventData, prevEvent)}
                                // `hasUnreadMention` is group-aware so the badge surfaces on the
                                // condensed root, but `mentionRef` stays gated by the raw event
                                // flag on purpose: the read is only acknowledged once the bubble
                                // that actually carries the mention scrolls into view, not when
                                // the root header alone is visible.
                                hasUnreadMention={unreadMentionGroupMap.get(item.data.id) ?? false}
                                mentionRef={eventData.has_unread_mention
                                    ? (el: HTMLDivElement | null) => { mentionRefs.current[item.data.id] = el; }
                                    : undefined
                                }
                            />
                        );
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
    // Show IM input when the user has at least edit rights on the mailbox
    // (editor/sender/admin), regardless of the thread-level role.
    // Still gated to shared contexts: either a shared mailbox or a thread
    // shared across multiple mailboxes.
    const hasMailboxEditAccess = !!selectedMailbox && (
        selectedMailbox.role === MailboxRoleChoices.editor
        || selectedMailbox.role === MailboxRoleChoices.sender
        || selectedMailbox.role === MailboxRoleChoices.admin
    );
    const hasMultipleAccesses = (selectedThread?.accesses?.length ?? 0) > 1;
    const isSharedMailbox = selectedMailbox?.is_identity === false;
    const showIMInput = Boolean((isSharedMailbox || hasMultipleAccesses) && hasMailboxEditAccess);

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

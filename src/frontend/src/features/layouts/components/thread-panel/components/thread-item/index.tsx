import { useTranslation } from "react-i18next"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import { useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import clsx from "clsx"
import { DateHelper } from "@/features/utils/date-helper"
import { Thread } from "@/features/api/gen/models"
import { ThreadItemSenders } from "./thread-item-senders"
import { Badge } from "@/features/ui/components/badge"
import { ThreadDragPreview } from "./thread-drag-preview"
import { PORTALS } from "@/features/config/constants"
import { Button, Checkbox, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { IconSize, IconType, UserAvatar } from "@gouvfr-lasuite/ui-kit"
import { AssigneesAvatarGroup } from "@/features/ui/components/assignees-avatar-group"
import { LabelBadge } from "@/features/ui/components/label-badge"
import { useLayoutDragContext } from "@/features/layouts/components/layout-context"
import ViewHelper from "@/features/utils/view-helper"
import useCanEditThreads from "@/features/message/use-can-edit-threads"
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature"
import { ThreadListboxItemProps } from "../../hooks/use-thread-listbox"
import { AttachFile, Edit, Star, StarFilled } from "@gouvfr-lasuite/ui-kit/icons"
import useStarred from "@/features/message/use-starred"
import useThreadUnread from "@/features/message/use-thread-unread"
import { useOpenDraftInWindow } from "@/features/message/use-open-draft-in-window"
import { useLongPress } from "@/hooks/use-long-press"
import { Icon } from "@/features/ui/components/icon"

/**
 * Shorter than the hook's default: nothing competes for the press anymore, so
 * selection mode can open as soon as the intent is unambiguous.
 */
const TOUCH_SELECTION_DELAY_MS = 350;

type ThreadItemProps = {
    thread: Thread
    isSelected: boolean
    onToggle: (threadId: string) => void
    onSelectRange: (threadId: string) => void
    selectedThreadIds: Set<string>
    isSelectionMode: boolean
} & ThreadListboxItemProps

export const ThreadItem = ({ thread, isSelected, onToggle, onSelectRange, selectedThreadIds, isSelectionMode, tabIndex, itemRef, onFocusItem }: ThreadItemProps) => {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const params = useParams({ strict: false }) as { mailboxId?: string; threadId?: string }
    const [isDragging, setIsDragging] = useState(false)
    const [isExiting, setIsExiting] = useState(false)
    // Read from `dragstart`, which can fire before a state update would have
    // been committed — hence a ref rather than state.
    const isTouchGestureRef = useRef(false)
    const { setIsDragging: setGlobalDragging, setDragAction } = useLayoutDragContext();
    const dragPreviewContainer = useRef(document.getElementById(PORTALS.DRAG_PREVIEW));
    const threadDate = useMemo(() => {
        if (ViewHelper.isInboxView() && thread.active_messaged_at) {
            return thread.active_messaged_at;
        }
        if (ViewHelper.isArchivedView() && thread.archived_messaged_at) {
            return thread.archived_messaged_at;
        }
        if (ViewHelper.isDraftsView() && thread.draft_messaged_at) {
            return thread.draft_messaged_at
        }
        if ((ViewHelper.isOutboxView() || ViewHelper.isSentView()) && thread.sender_messaged_at) {
            return thread.sender_messaged_at;
        }
        if (ViewHelper.isTrashedView() && thread.trashed_messaged_at) {
            return thread.trashed_messaged_at;
        }

        // Draft-only threads have messaged_at=null, fall back to draft_messaged_at
        return thread.messaged_at || thread.draft_messaged_at;
    }, [thread])

    const hasUnread = useThreadUnread(thread);

    const hasSelection = isSelectionMode || selectedThreadIds.size > 0;
    const showCheckbox = hasSelection;

    // Used by drop zones (folders, label auto-archive) to decide whether
    // the dragged threads can be mutated. Expressed as a dataTransfer
    // type so drop zones can read it on dragover — JSON payloads are only
    // readable on drop per the browser security model.
    const dragThreadIds = useMemo(
        () => isSelectionMode ? Array.from(selectedThreadIds) : [thread.id],
        [isSelectionMode, selectedThreadIds, thread.id]
    );
    const hasEditableInDrag = useCanEditThreads(dragThreadIds);
    const snippetEnabled = useFeatureFlag(FEATURE_KEYS.THREAD_SNIPPET);
    const showSnippet = snippetEnabled && !!thread.snippet;

    // ``sender_names`` is stored server-side as [first sender, last sender]
    // (collapsed to a single entry when both are the same), so the avatar of
    // the most recent sender is always the last entry.
    const lastSenderName = thread.sender_names?.at(-1);

    // A long press is the touch entry point into selection mode — the only
    // one, since dragging is mouse-only. The tap that ends the press still
    // fires a click afterwards, which would navigate to the thread we just
    // selected — the flag swallows exactly that one click.
    const suppressNextClickRef = useRef(false);
    const { handlers: longPressHandlers } = useLongPress(() => {
        suppressNextClickRef.current = true;
        onToggle(thread.id);
    }, { delay: TOUCH_SELECTION_DELAY_MS });

    // Keyboard activation (Enter on the focused option) fires a click with
    // detail === 0 and no pointer type: it must navigate even while a
    // selection is active, being the only pointer-free way to open a thread.
    // Taps report detail === 0 too on some WebKit builds, hence the
    // pointerType check — without it a tap on a selected thread would be
    // mistaken for a keyboard activation and open the thread.
    const isKeyboardActivation = (e: React.MouseEvent<HTMLDivElement>) => {
        const pointerType = 'pointerType' in e.nativeEvent
            ? (e.nativeEvent as PointerEvent).pointerType
            : '';
        return e.detail === 0 && !pointerType;
    };

    // Gmail-style pointer entry into selection mode: a press on the identity
    // slot (avatar, or checkbox once a selection exists) toggles the thread
    // instead of opening it. The slot is raised above the stretched link
    // overlay (see SCSS), so these clicks land on the slot itself and never
    // trigger the Link — only the bubble-phase handler below sees them.
    const isIdentitySlotClick = (e: React.MouseEvent<HTMLDivElement>) =>
        e.target instanceof Element && !!e.target.closest('.thread-item__identity-slot');

    // A thread whose only message is a draft opens the compose window
    // directly (like Gmail) instead of navigating to a mostly-empty thread
    // view. The list does not know the draft id: the hook resolves it.
    const isDraftOnlyThread = !thread.messaged_at && thread.has_draft;
    const { openThreadDraftInWindow } = useOpenDraftInWindow();

    // "Opening" clicks for a draft-only thread: the stretched subject link or
    // any non-interactive area. Other links/buttons (labels, star…) keep
    // their own behavior.
    const isThreadOpeningClick = (e: React.MouseEvent<HTMLDivElement>) =>
        e.target instanceof Element && !e.target.closest('a:not(.thread-item__link), button');

    // Cancelling the navigation has to happen on the way down: the Link
    // navigates from its own onClick handler on the <a>, which runs before
    // the event bubbles up to this container. It skips navigation when the
    // event is already defaultPrevented — and for ctrl/meta/shift clicks,
    // which is why modifier-driven selection worked without this, while a
    // plain tap in selection mode still opened the thread.
    const handleItemClickCapture = (e: React.MouseEvent<HTMLDivElement>) => {
        if (isKeyboardActivation(e)) {
            // Keyboard "open" on a draft-only thread must not navigate
            // either: the bubble handler opens the compose window.
            if (isDraftOnlyThread) e.preventDefault();
            return;
        }
        if (suppressNextClickRef.current || hasSelection || e.shiftKey || e.ctrlKey || e.metaKey) {
            e.preventDefault();
        } else if (isDraftOnlyThread && isThreadOpeningClick(e)) {
            e.preventDefault();
        }
    };

    // Lives on the item container, not the Link: it catches clicks bubbling
    // from the stretched ::after overlay as well as clicks on elements raised
    // above it (badges column, labels).
    const handleItemClick = (e: React.MouseEvent<HTMLDivElement>) => {
        onFocusItem();
        if (suppressNextClickRef.current) {
            suppressNextClickRef.current = false;
            e.preventDefault();
            return;
        }
        if (isKeyboardActivation(e)) {
            if (isDraftOnlyThread) void openThreadDraftInWindow(thread);
            return;
        }
        if (e.shiftKey) {
            e.preventDefault();
            onSelectRange(thread.id);
        } else if (e.ctrlKey || e.metaKey || hasSelection || isIdentitySlotClick(e)) {
            e.preventDefault();
            onToggle(thread.id);
        } else if (isDraftOnlyThread && isThreadOpeningClick(e)) {
            void openThreadDraftInWindow(thread);
        } else if (e.target instanceof Element && !e.target.closest('a, button')) {
            // Raised elements sit above the stretched link, so plain clicks
            // on them never reach it: navigate as the link would have.
            navigate({
                to: "/mailbox/$mailboxId/thread/$threadId",
                params: { mailboxId: params?.mailboxId ?? '', threadId: thread.id },
                search: true,
            });
        }
        // Otherwise, let the Link handle navigation normally
    };

    const handleDragStart = (e: React.DragEvent<HTMLDivElement>) => {
        // Dragging is mouse-only: browsers start their touch drag on the hold
        // alone, with no way to make it wait for a movement, so it claimed
        // every long press meant to open selection mode. Refusing the
        // `dragstart` here rather than dropping `draggable` on the container
        // is what actually works — the event bubbles up from the subject
        // `<a>`, which anchors make draggable with or without the attribute.
        if (isTouchGestureRef.current) {
            e.preventDefault();
            return;
        }
        setIsDragging(true)
        setGlobalDragging(true)

        e.dataTransfer.setData('application/json', JSON.stringify({
            type: 'thread',
            threadIds: dragThreadIds,
            labels: isSelectionMode ? [] : thread.labels.map((label) => label.id),
            hasEditable: hasEditableInDrag,
        }));
        e.dataTransfer.setData('text/thread-drag', '');
        // Advertised on dragover so folder drop zones can refuse drops
        // when the dragged selection has no editable thread (archive,
        // spam, trash and restore-to-inbox all require edit rights).
        if (hasEditableInDrag) {
            e.dataTransfer.setData('text/thread-editable', '');
        }

        // Hide native drag image by using an offscreen empty element as drag image
        // (Safari-compatible — `new Image()` with an inline data URL is unreliable
        // because Safari requires the image to be loaded/in the DOM).
        // The ThreadDragPreview portal follows the cursor instead.
        const ghost = document.createElement('div');
        ghost.style.cssText = 'position:absolute;top:-1000px;left:-1000px;width:1px;height:1px;';
        document.body.appendChild(ghost);
        e.dataTransfer.setDragImage(ghost, 0, 0);
        setTimeout(() => document.body.removeChild(ghost), 0);
    }
    const handleDragEnd = () => {
        setIsDragging(false);
        setGlobalDragging(false);
        setIsExiting(true);
    };

    const handleExitEnd = () => {
        setIsExiting(false);
        setDragAction(null);
    };

    const { markAsStarred, markAsUnstarred } = useStarred();
    // Starring a trashed or spam thread makes no sense: keep the starred
    // state visible (read-only badge) but drop the toggle in those views.
    const canToggleStar = !ViewHelper.isTrashedView() && !ViewHelper.isSpamView();

    const dragCount = selectedThreadIds.size > 0 ? selectedThreadIds.size : 1;

    // Clear any pending drag action if the item unmounts before the
    // exit animation completes (e.g. archived after label assignment),
    // otherwise the stale action would leak into the next drag preview.
    useEffect(() => () => setDragAction(null), [setDragAction]);


    // The link (the listbox option) only wraps the thread subject, with an
    // accessible name built from senders + subject, and stretches its click
    // surface over the whole item via a ::after overlay (see SCSS). Nesting
    // the whole item inside the link would put the star button — an
    // interactive element — inside another interactive element.
    // Date, badges and labels sit outside the link, so they feed the
    // option's *description* instead: screen readers still announce them
    // after the name, while voice-control tools keep targeting the short
    // name only.
    const sendersId = `thread-item-senders-${thread.id}`;
    const subjectId = `thread-item-subject-${thread.id}`;
    const dateId = `thread-item-date-${thread.id}`;
    const badgesId = `thread-item-badges-${thread.id}`;
    const labelsId = `thread-item-labels-${thread.id}`;

    return (
        <>
            <div
                className={clsx(
                    'thread-item',
                    {
                        'thread-item--active': thread.id === params?.threadId,
                        'thread-item--dragging': isDragging,
                        'thread-item--selected': isSelected,
                    },
                )}
                data-unread={hasUnread}
                draggable
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
                onClickCapture={handleItemClickCapture}
                onClick={handleItemClick}
                {...longPressHandlers}
                onTouchStart={(e) => {
                    // A press that never produces a click (finger lifted after
                    // a scroll, gesture cancelled) would otherwise leave the
                    // flag armed and swallow the next tap.
                    suppressNextClickRef.current = false;
                    isTouchGestureRef.current = true;
                    longPressHandlers.onTouchStart(e);
                }}
                onTouchEnd={() => {
                    isTouchGestureRef.current = false;
                    longPressHandlers.onTouchEnd();
                }}
                // Deliberately leaves the touch flag armed: browsers fire
                // `touchcancel` precisely when they claim the gesture for a
                // drag, and nothing guarantees it arrives after `dragstart` —
                // disarming here could hand them back the press. The next
                // `touchstart` rearms it anyway.
                onTouchCancel={longPressHandlers.onTouchCancel}
            >
                <div className="thread-item__aside">
                    <div className="thread-item__read-indicator" />
                    {/* The checkbox takes over the avatar slot in selection
                        mode: both are the row's leading affordance, and
                        stacking them would shift the whole row sideways every
                        time selection is toggled. */}
                    <div className="thread-item__identity-slot">
                        {showCheckbox ? (
                            // Pointer-only affordance, purely presentational: the option
                            // itself carries the selection state (aria-selected) and a
                            // focusable checkbox would add a second tab stop, hence
                            // aria-hidden + tabIndex=-1. pointer-events:none (see SCSS)
                            // lets the click fall through to the item container, where
                            // handleItemClick owns the selection logic; the checkbox
                            // only reflects `checked`. Driving it from its own click
                            // handler would fight handleItemClick's preventDefault and
                            // leave it visually out of sync.
                            <span aria-hidden="true" className="thread-item__checkbox-wrapper">
                                <Checkbox
                                    checked={isSelected}
                                    tabIndex={-1}
                                    className="thread-item__checkbox"
                                />
                            </span>
                        ) : lastSenderName && (
                            // Decorative: the sender is already announced as part of
                            // the option's accessible name.
                            <span aria-hidden="true" className="thread-item__avatar">
                                <UserAvatar fullName={lastSenderName} size="medium" />
                            </span>
                        )}
                    </div>
                </div>
                <div>
                    <div className="thread-item__row">
                        <div className="thread-item__column" id={sendersId}>
                            {thread.sender_names && thread.sender_names.length > 0 && (
                                <ThreadItemSenders senders={thread.sender_names} />
                            )}
                            {thread.active_messages_count > 1 &&
                                <span
                                    className="thread-item__message-count"
                                    aria-label={t('{{count}} messages', { count: thread.active_messages_count })}
                                >
                                    {thread.active_messages_count}
                                </span>
                            }
                        </div>
                        <div className="thread-item__column thread-item__column--metadata" id={dateId}>
                            {(threadDate || thread.messaged_at) && (
                                <span className="thread-item__date">
                                    {DateHelper.formatDate((threadDate || thread.messaged_at)!, i18n.resolvedLanguage)}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="thread-item__row thread-item__row--subject">
                        <div className="thread-item__column">
                            <Link
                                to="/mailbox/$mailboxId/thread/$threadId"
                                params={{ mailboxId: params?.mailboxId ?? '', threadId: thread.id }}
                                search={true}
                                className="thread-item__link"
                                aria-labelledby={`${sendersId} ${subjectId}`}
                                aria-describedby={`${dateId} ${badgesId} ${labelsId}`}
                                onFocus={onFocusItem}
                                tabIndex={tabIndex}
                                ref={itemRef}
                                role="option"
                                aria-selected={isSelected}
                            >
                                <p className="thread-item__subject" id={subjectId}>{thread.subject || t('No subject')}</p>
                            </Link>
                        </div>
                        <div className="thread-item__column thread-item__column--badges" id={badgesId}>
                            {thread.has_draft && (
                                <Badge aria-label={t('Draft')} title={t('Draft')} color="neutral" variant="tertiary" compact>
                                    <Icon
                                        icon={Edit}
                                        size={IconSize.SMALL}
                                    />
                                </Badge>
                            )}
                            {thread.has_attachments ? (
                                <Badge
                                    aria-label={t('Attachments')}
                                    title={t('Attachments')}
                                    color="neutral"
                                    variant="tertiary"
                                    compact>
                                    <Icon icon={AttachFile} size={IconSize.SMALL} />
                                </Badge>
                            ) : null}
                            {thread.has_unread_mention && (
                                <Badge
                                    aria-label={t('Unread mention')}
                                    title={t('Unread mention')}
                                    color="warning"
                                    variant="tertiary"
                                    compact>
                                    <Icon
                                        type={IconType.OUTLINED}
                                        name="alternate_email"
                                        className="icon--size-sm"
                                    />
                                </Badge>
                            )}
                            {thread.has_delivery_failed && (
                                <Badge
                                    aria-label={t('Delivery failed')}
                                    title={t('Some recipients have not received this message!')}
                                    color="error"
                                    variant="tertiary"
                                    compact>
                                    <Icon
                                        name="error"
                                        type={IconType.OUTLINED}
                                        size={IconSize.SMALL}
                                    />
                                </Badge>
                            )}
                            {!thread.has_delivery_failed && thread.has_delivery_pending && (
                                <Badge
                                    aria-label={t('Delivering')}
                                    title={t('This message has not yet been delivered to all recipients.')}
                                    color="warning"
                                    variant="tertiary"
                                    compact>
                                    <Icon
                                        name="update"
                                        type={IconType.OUTLINED}
                                        size={IconSize.SMALL}
                                    />
                                </Badge>
                            )}
                            {thread.assigned_users.length > 0 && (
                                <Tooltip
                                    content={t('Assigned to {{names}}', {
                                        names: thread.assigned_users.map((u) => u.name).join(', '),
                                    })}
                                >
                                    <span
                                        aria-label={t('Assigned to {{names}}', {
                                            names: thread.assigned_users.map((u) => u.name).join(', '),
                                        })}
                                    >
                                        <AssigneesAvatarGroup
                                            users={thread.assigned_users}
                                            maxAvatars={2}
                                            overflowMode="replace-last"
                                        />
                                    </span>
                                </Tooltip>
                            )}
                            {canToggleStar ? (
                                thread.has_starred ? (
                                    <Button
                                        aria-label={t('Unstar this thread')}
                                        title={t('Unstar this thread')}
                                        color="warning"
                                        variant="tertiary"
                                        size="nano"
                                        onClick={(e) => {
                                            e.preventDefault();
                                            e.stopPropagation();
                                            markAsUnstarred({ threadIds: [thread.id] });
                                        }}
                                        icon={<StarFilled size={IconSize.SMALL} aria-hidden="true" />}
                                    />
                                ) : (
                                    <Button
                                        aria-label={t('Star this thread')}
                                        title={t('Star this thread')}
                                        color="neutral"
                                        variant="tertiary"
                                        size="nano"
                                        onClick={(e) => {
                                            e.preventDefault();
                                            e.stopPropagation();
                                            markAsStarred({ threadIds: [thread.id] });
                                        }}
                                        icon={<Star size={IconSize.SMALL} aria-hidden="true" />}
                                    />
                                )
                            ) : thread.has_starred && (
                                <Badge aria-label={t('Starred')} title={t('Starred')} color="warning" variant="tertiary" compact>
                                    <StarFilled size={IconSize.SMALL} aria-hidden="true" />
                                </Badge>
                            )}
                        </div>
                    </div>
                    {showSnippet && (
                        <div className="thread-item__row thread-item__snippet">
                            {thread.snippet}
                        </div>
                    )}
                    <div className="thread-item__row">
                        {thread.labels.length > 0 && (
                            <div className="thread-item__labels">
                                {thread.labels.map((label) => (
                                    <LabelBadge key={label.id} label={label} compact />
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
            {(isDragging || isExiting) && dragPreviewContainer.current && createPortal(
                <ThreadDragPreview
                    count={dragCount}
                    exiting={isExiting}
                    onExitEnd={handleExitEnd}
                />,
                dragPreviewContainer.current
            )}
        </>
    )
}

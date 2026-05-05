import { useTranslation } from "react-i18next"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import clsx from "clsx"
import { DateHelper } from "@/features/utils/date-helper"
import { Thread } from "@/features/api/gen/models"
import { ThreadItemSenders } from "./thread-item-senders"
import { Badge } from "@/features/ui/components/badge"
import { ThreadDragPreview } from "./thread-drag-preview"
import { PORTALS } from "@/features/config/constants"
import { Checkbox, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit"
import { AssigneesAvatarGroup } from "@/features/ui/components/assignees-avatar-group"
import { LabelBadge } from "@/features/ui/components/label-badge"
import { useLayoutDragContext } from "@/features/layouts/components/layout-context"
import ViewHelper from "@/features/utils/view-helper"
import useCanEditThreads from "@/features/message/use-can-edit-threads"

type ThreadItemProps = {
    thread: Thread
    isSelected: boolean
    onToggleSelection: (threadId: string, shiftKey: boolean, ctrlKey: boolean, arrowUpKey?: 'up' | 'down') => void
    selectedThreadIds: Set<string>
    isSelectionMode: boolean
}

export const ThreadItem = ({ thread, isSelected, onToggleSelection, selectedThreadIds, isSelectionMode }: ThreadItemProps) => {
    const { t, i18n } = useTranslation();
    const params = useParams<{ mailboxId: string, threadId: string }>()
    const searchParams = useSearchParams()
    const [isDragging, setIsDragging] = useState(false)
    const [isExiting, setIsExiting] = useState(false)
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

    const hasUnread = useMemo(() => {
        const access = thread.accesses.find((a) => a.mailbox.id === params?.mailboxId)
        const compareDate = thread.messaged_at;
        if (!access || !compareDate) return false
        if (!access.read_at) return true
        return new Date(compareDate) > new Date(access.read_at)
    }, [thread, params?.mailboxId])

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

    const handleCheckboxClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        onToggleSelection(thread.id, e.shiftKey, e.ctrlKey || e.metaKey);
    };

    const handleItemClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        // If using modifier keys or in selection mode, toggle selection instead of navigating
        if (e.shiftKey || e.ctrlKey || e.metaKey || hasSelection) {
            e.preventDefault();
            onToggleSelection(thread.id, e.shiftKey, e.ctrlKey || e.metaKey);
        }
        // Otherwise, let the Link handle navigation normally
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLAnchorElement>) => {
        if (!hasSelection) return;
        const arrowUpKey = e.key === 'ArrowUp';
        const arrowDownKey = e.key === 'ArrowDown';
        if (e.shiftKey && (arrowUpKey || arrowDownKey)) {
            e.preventDefault();
            onToggleSelection(thread.id, e.shiftKey, e.ctrlKey || e.metaKey, arrowUpKey ? 'up' : 'down');
        }
    };

    const handleDragStart = (e: React.DragEvent<HTMLAnchorElement>) => {
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

    const dragCount = selectedThreadIds.size > 0 ? selectedThreadIds.size : 1;

    // Clear any pending drag action if the item unmounts before the
    // exit animation completes (e.g. archived after label assignment),
    // otherwise the stale action would leak into the next drag preview.
    useEffect(() => () => setDragAction(null), [setDragAction]);


    return (
        <>
            <Link
                href={`/mailbox/${params?.mailboxId}/thread/${thread.id}?${searchParams}`}
                className={clsx(
                    'thread-item',
                    {
                        'thread-item--active': thread.id === params?.threadId,
                        'thread-item--dragging': isDragging,
                        'thread-item--selected': isSelected,
                    },
                )}
                data-thread-id={thread.id}
                data-unread={hasUnread}
                draggable
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
                onClick={handleItemClick}
                onKeyDown={handleKeyDown}
                tabIndex={0}
            >
                <div>
                    {showCheckbox && (
                        <Checkbox
                            checked={isSelected}
                            onClick={handleCheckboxClick}
                            aria-label={isSelected ? t('Deselect thread') : t('Select thread')}
                            className="thread-item__checkbox"
                        />
                    )}
                    <div className="thread-item__read-indicator" />
                </div>
                <div>
                    <div className="thread-item__row">
                        <div className="thread-item__column">
                            {thread.sender_names && thread.sender_names.length > 0 && (
                                <ThreadItemSenders senders={thread.sender_names} />
                            )}
                        </div>
                        <div className="thread-item__column thread-item__column--metadata">
                            {(threadDate || thread.messaged_at) && (
                                <span className="thread-item__date">
                                    {DateHelper.formatDate((threadDate || thread.messaged_at)!, i18n.resolvedLanguage)}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="thread-item__row thread-item__row--subject">
                        <div className="thread-item__column">
                            <p className="thread-item__subject">{thread.subject || t('No subject')}</p>
                        </div>
                        <div className="thread-item__column thread-item__column--badges">
                            {thread.has_draft && (
                                <Badge aria-label={t('Draft')} title={t('Draft')} color="neutral" variant="tertiary" compact>
                                    <Icon
                                        type={IconType.FILLED}
                                        name="mode_edit"
                                        className="icon--size-sm"
                                        aria-hidden="true"
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
                                    <Icon
                                        name="attachment"
                                        size={IconSize.SMALL}
                                        aria-hidden="true"
                                    />
                                </Badge>
                            ) : null}
                            {thread.has_starred && (
                                <Badge
                                    aria-label={t('Starred')}
                                    title={t('Starred')}
                                    color="yellow"
                                    variant="tertiary"
                                    compact>
                                    <Icon
                                        name="star"
                                        size={IconSize.SMALL}
                                        aria-hidden="true"
                                    />
                                </Badge>
                            )}
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
                                        aria-hidden="true"
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
                                        aria-hidden="true"
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
                                        aria-hidden="true"
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
                        </div>
                    </div>
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
            </Link>
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

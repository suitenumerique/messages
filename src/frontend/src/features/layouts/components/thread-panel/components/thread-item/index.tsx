import { useTranslation } from "react-i18next"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { useRef, useState } from "react"
import { createPortal } from "react-dom"
import clsx from "clsx"
import { DateHelper } from "@/features/utils/date-helper"
import { Thread } from "@/features/api/gen/models"
import { ThreadItemSenders } from "./thread-item-senders"
import { Badge } from "@/features/ui/components/badge"
import { ThreadDragPreview } from "./thread-drag-preview"
import { PORTALS } from "@/features/config/constants"
import { Checkbox } from "@gouvfr-lasuite/cunningham-react"
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit"
import { LabelBadge } from "@/features/ui/components/label-badge"
import { useLayoutContext } from "../../../main"

type ThreadItemProps = {
    thread: Thread
    index: number
    isSelected: boolean
    onToggleSelection: (threadId: string, index: number, shiftKey: boolean, ctrlKey: boolean, arrowUpKey?: 'up' | 'down') => void
    selectedThreadIds: Set<string>
    isSelectionMode: boolean
}

export const ThreadItem = ({ thread, index, isSelected, onToggleSelection, selectedThreadIds, isSelectionMode }: ThreadItemProps) => {
    const { t, i18n } = useTranslation();
    const params = useParams<{ mailboxId: string, threadId: string }>()
    const searchParams = useSearchParams()
    const [isDragging, setIsDragging] = useState(false)
    const { setIsDragging: setGlobalDragging } = useLayoutContext();
    const dragPreviewContainer = useRef(document.getElementById(PORTALS.DRAG_PREVIEW));

    const hasSelection = isSelectionMode || selectedThreadIds.size > 0;
    const showCheckbox = hasSelection;

    const handleCheckboxClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        onToggleSelection(thread.id, index, e.shiftKey, e.ctrlKey || e.metaKey);
    };

    const handleItemClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        // If using modifier keys or in selection mode, toggle selection instead of navigating
        if (e.shiftKey || e.ctrlKey || e.metaKey || hasSelection) {
            e.preventDefault();
            onToggleSelection(thread.id, index, e.shiftKey, e.ctrlKey || e.metaKey);
        }
        // Otherwise, let the Link handle navigation normally
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLAnchorElement>) => {
        if (!hasSelection) return;
        const arrowUpKey = e.key === 'ArrowUp';
        const arrowDownKey = e.key === 'ArrowDown';
        if (e.shiftKey && (arrowUpKey || arrowDownKey)) {
            e.preventDefault();
            onToggleSelection(thread.id, index, e.shiftKey, e.ctrlKey || e.metaKey, arrowUpKey ? 'up' : 'down');
        }
    };

    const handleDragStart = (e: React.DragEvent<HTMLAnchorElement>) => {
        setIsDragging(true)
        setGlobalDragging(true)

        // If this thread is selected, drag all selected threads
        const threadsToDrag = isSelectionMode ? Array.from(selectedThreadIds) : [thread.id];

        e.dataTransfer.setData('application/json', JSON.stringify({
            type: 'thread',
            threadIds: threadsToDrag,
            labels: isSelectionMode ? [] : thread.labels.map((label) => label.id),
        }));
        e.dataTransfer.effectAllowed = 'link'
        // Set the drag image
        if (dragPreviewContainer.current) {
            e.dataTransfer.setDragImage(dragPreviewContainer.current, 40, 40)
        }
    }
    const handleDragEnd = () => {
        setIsDragging(false);
        setGlobalDragging(false);
    };

    const dragCount = selectedThreadIds.size > 0 ? selectedThreadIds.size : 1;

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
                data-unread={thread.has_unread}
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
                            {/* <Tooltip content={thread.labels.map((label) => label.display_name).join(', ')}>
                                <div className="thread-item__label-bullets">
                                    {thread.labels.slice(0, 4).map((label) => (
                                        <div key={`label-bullet-${label.id}`} className="thread-item__label-bullet" style={{ backgroundColor: label.color }} />
                                    ))}
                                    {thread.labels.length > 4 && (
                                        <div className="thread-item__label-bullet">
                                            +{thread.labels.length - 4}
                                        </div>
                                    )}
                                </div>
                            </Tooltip> */}

                            {thread.messaged_at && (
                                <span className="thread-item__date">
                                    {DateHelper.formatDate(thread.messaged_at, i18n.resolvedLanguage)}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="thread-item__row thread-item__row--subject">
                        <div className="thread-item__column">
                            <p className="thread-item__subject">{thread.subject || thread.snippet || t('No subject')}</p>
                        </div>
                        <div className="thread-item__column thread-item__column--badges">
                            {thread.has_draft && (
                                <Badge aria-label={t('Draft')} title={t('Draft')} color="neutral" variant="tertiary" compact>
                                    <Icon
                                        type={IconType.FILLED}
                                        name="mode_edit"
                                        className="icon--size-sm"
                                    />
                                </Badge>
                            )}
                            {thread.has_attachments ? (
                                <Badge aria-label={t('Attachments')} title={t('Attachments')} color="neutral" variant="tertiary" compact>
                                    <Icon name="attachment" size={IconSize.SMALL} />
                                </Badge>
                            ) : null}
                            {/* <div className="thread-item__actions">
                        <Tooltip placement="bottom" content={t('Mark as important')}>
                            <Button color="tertiary-text" className="thread-item__action">
                                <span className="material-icons">
                                    flag
                                </span>
                            </Button>
                        </Tooltip>
                    </div> */}
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
            {isDragging && dragPreviewContainer.current && createPortal(
                <ThreadDragPreview count={dragCount} />,
                dragPreviewContainer.current
            )}
        </>
    )
}

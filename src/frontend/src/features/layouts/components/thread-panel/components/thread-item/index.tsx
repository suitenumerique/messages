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
import { LabelBadge } from "@/features/ui/components/label-badge"
import { ThreadDragPreview } from "./thread-drag-preview"
import { PORTALS } from "@/features/config/constants"

type ThreadItemProps = {
    thread: Thread
}

export const ThreadItem = ({ thread }: ThreadItemProps) => {
    const { t, i18n } = useTranslation();
    const params = useParams<{mailboxId: string, threadId: string}>()
    const searchParams = useSearchParams()
    const [isDragging, setIsDragging] = useState(false)
    const dragPreviewContainer = useRef(document.getElementById(PORTALS.DRAG_PREVIEW));

    const handleDragStart = (e: React.DragEvent<HTMLAnchorElement>) => {
        setIsDragging(true)
        e.dataTransfer.setData('application/json', JSON.stringify({
            type: 'thread',
            threadId: thread.id,
            labels: thread.labels.map((label) => label.id),
        }))
        e.dataTransfer.effectAllowed = 'link'
        // Set the drag image
        if (dragPreviewContainer.current) {
            e.dataTransfer.setDragImage(dragPreviewContainer.current, 40, 40)
        }
    }
    const handleDragEnd = () => setIsDragging(false);

    return (
        <>
            <Link
                href={`/mailbox/${params?.mailboxId}/thread/${thread.id}?${searchParams}`}
                className={clsx(
                    'thread-item',
                    {
                        'thread-item--active': thread.id === params?.threadId,
                        'thread-item--dragging': isDragging,
                    },
                )}
                data-unread={thread.has_unread}
                draggable
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
            >
                <div className="thread-item__left">
                    <div className="thread-item__read-indicator" />
                    <div className="thread-item__thread-details">
                        <div className="thread-item__sender-info">
                            {thread.sender_names && thread.sender_names.length > 0 && (
                                <ThreadItemSenders
                                    senders={thread.sender_names}
                                    isUnread={thread.has_unread}
                                    messagesCount={thread.messages.length}
                                />
                            )}
                            <div className="thread-item__metadata">
                                {thread.has_draft && (
                                    <Badge>
                                        {t('thread_message.draft')}
                                    </Badge>
                                )}
                        {/* {thread.has_attachments ? (
                                <span className="thread-item__metadata-attachments">
                                    <Tooltip placement="bottom" content={t('tooltips.has_attachments')}>
                                        <span className="material-icons">attachment</span>
                                    </Tooltip>
                                </span>
                            ) : null} */}
                            </div>
                        </div>
                        <div className="thread-item__content">
                            <p className="thread-item__subject">{thread.subject}</p>
                            {thread.labels.length > 0 && (
                                <div className="thread-item__labels">
                                    {thread.labels.map((label) => (
                                        <LabelBadge key={label.id} label={label} />
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
                <div className="thread-item__right">
                {/* <div className="thread-item__actions">
                    <Tooltip placement="bottom" content={t('actions.mark_as_important')}>
                        <Button color="tertiary-text" className="thread-item__action">
                            <span className="material-icons">
                                flag
                            </span>
                        </Button>
                    </Tooltip>
                </div> */}
                    {thread.messaged_at && (
                        <span className="thread-item__date">
                            {DateHelper.formatDate(thread.messaged_at, i18n.language)}
                        </span>
                    )}
                </div>
            </Link>
            {isDragging && dragPreviewContainer.current && createPortal(
                <ThreadDragPreview thread={thread} />,
                dragPreviewContainer.current
            )}
        </>
    )
}

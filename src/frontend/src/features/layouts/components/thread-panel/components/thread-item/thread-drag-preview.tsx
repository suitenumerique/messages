import { Thread } from "@/features/api/gen/models/thread"
import { ThreadItemSenders } from "./thread-item-senders"
import { LabelBadge } from "@/features/ui/components/label-badge"
import { useTranslation } from "react-i18next";

/**
 * This component is used to display a preview of a thread when it is being dragged.
 * It aims to be rendered within the portal dedicated to drag preview '#drag-preview-container''
 * Take a look at `_document.tsx`
 */
export const ThreadDragPreview = ({ thread }: { thread: Thread }) => {
    const { t } = useTranslation();
    return (
        <div className="thread-drag-preview">
            <div className="thread-drag-preview__content">
                <div className="thread-drag-preview__senders">
                    {thread.sender_names && thread.sender_names.length > 0 && (
                        <ThreadItemSenders
                            senders={thread.sender_names}
                            isUnread={thread.has_unread}
                            messagesCount={thread.messages.length}
                        />
                    )}
                </div>
                <div className="thread-item__subject">
                    {thread.subject || thread.snippet || t('thread-item.no-subject')}
                </div>
                {thread.labels.length > 0 && (
                    <div className="thread-drag-preview__labels">
                        {thread.labels.map((label) => (
                            <LabelBadge
                                key={label.id}
                                label={label}
                            />
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

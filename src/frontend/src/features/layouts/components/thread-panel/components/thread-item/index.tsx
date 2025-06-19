import { useTranslation } from "react-i18next"
import { DateHelper } from "@/features/utils/date-helper"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { Thread } from "@/features/api/gen/models"
import { ThreadItemSenders } from "./thread-item-senders"
import { Badge } from "@/features/ui/components/badge"
import { LabelBadge } from "@/features/ui/components/label-badge"

type ThreadItemProps = {
    thread: Thread
}

export const ThreadItem = ({ thread }: ThreadItemProps) => {
    const { t, i18n } = useTranslation();
    const params = useParams<{mailboxId: string, threadId: string}>()
    const searchParams = useSearchParams()

    return (
        <Link
            href={`/mailbox/${params?.mailboxId}/thread/${thread.id}?${searchParams}`}
            className={`thread-item ${thread.id === params?.threadId && "thread-item--active"} `}
            data-unread={thread.has_unread}
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
    )
}

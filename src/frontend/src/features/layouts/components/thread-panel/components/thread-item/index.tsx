import { useTranslation } from "react-i18next"
import { DateHelper } from "@/features/utils/date-helper"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { Thread } from "@/features/api/gen/models"
import { ThreadItemSenders } from "./thread-item-senders"
import ThreadHelper from "@/features/utils/thread-helper"
import { LabelBadge } from "@/features/layouts/components/label-badge"

type ThreadItemProps = {
    thread: Thread
}

export const ThreadItem = ({ thread }: ThreadItemProps) => {
    const { i18n } = useTranslation();
    const params = useParams<{mailboxId: string, threadId: string}>()
    const isUnread = ThreadHelper.isUnread(thread, true)
    const searchParams = useSearchParams()
    
    return (
        <Link
            href={`/mailbox/${params?.mailboxId}/thread/${thread.id}?${searchParams}`}
            className={`thread-item ${thread.id === params?.threadId && "thread-item--active"} `}
            data-unread={isUnread}
        >
            <div className="thread-item__left">
                <div className="thread-item__read-indicator" />
                <div className="thread-item__thread-details">
                    <div className="thread-item__sender-info">
                        {thread.sender_names && thread.sender_names.length > 0 && (
                            <ThreadItemSenders
                                senders={thread.sender_names}
                                isUnread={ThreadHelper.isUnread(thread, false)}
                                messagesCount={thread.count_messages ?? 0}
                            />
                        )}
                        <div className="thread-item__metadata">
                            {thread.labels && thread.labels.length > 0 && (
                                <div className="thread-item__labels">
                                    {thread.labels.map((label) => (
                                        <LabelBadge key={label.id} label={label} size="small" />
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                    <p className="thread-item__subject">{thread.subject}</p>
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
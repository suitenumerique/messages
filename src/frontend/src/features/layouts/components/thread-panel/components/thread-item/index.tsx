import { useTranslation } from "react-i18next"
import { DateHelper } from "@/features/utils/date-helper"
import { Button, Tooltip } from "@openfun/cunningham-react"
import Link from "next/link"
import { useParams } from "next/navigation"

export const ThreadItem = ({ thread }) => {
    const { t, i18n } = useTranslation();

    const {mailboxId, threadId} = useParams<{mailboxId: string, threadId: string}>()
    
    return (
        <Link
            href={`/mailbox/${mailboxId}/thread/${thread.id}`}
            className={`thread-item ${thread.id === threadId && "thread-item--active"} `}
        >
            <div className="thread-item__left">
                <div className="thread-item__read-indicator" data-unread={thread.is_unread} />
                <div className="thread-item__thread-details">
                    <div className="thread-item__sender-info">
                        <p className="thread-item__sender"><strong>{thread.sender_name}</strong></p>
                        <div className="thread-item__metadata">
                            {thread.has_attachments ? (
                                <span className="thread-item__metadata-attachments">
                                    <Tooltip placement="bottom" content={t('tooltips.has_attachments')}>
                                        <span className="material-icons">attachment</span>
                                    </Tooltip>
                                </span>
                            ) : null}
                        </div>
                    </div>
                    <p className="thread-item__subject">{thread.subject}</p>
                </div>
            </div>
            <div className="thread-item__right">
                <div className="thread-item__actions">
                    <Tooltip placement="bottom" content={t('actions.mark_as_important')}>
                        <Button color="tertiary-text" className="thread-item__action">
                            <span className="material-icons">
                                flag
                            </span>
                        </Button>
                    </Tooltip>
                </div>
                <span className="thread-item__date">
                    {DateHelper.formatDate(thread.sent_at, i18n.language)}
                </span>
            </div>
        </Link>
    )
}
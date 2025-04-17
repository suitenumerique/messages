import { useTranslation } from "react-i18next"
import { DateHelper } from "@/features/utils/date-helper"
import { Button, Tooltip } from "@openfun/cunningham-react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { Thread } from "@/features/api/gen/models"
import { ThreadItemRecipients } from "./thread-item-recipients"

type ThreadItemProps = {
    thread: Thread
}

export const ThreadItem = ({ thread }: ThreadItemProps) => {
    const { t, i18n } = useTranslation();
    const {mailboxId, threadId} = useParams<{mailboxId: string, threadId: string}>()
    
    return (
        <Link
            href={`/mailbox/${mailboxId}/thread/${thread.id}`}
            className={`thread-item ${thread.id === threadId && "thread-item--active"} `}
        >
            <div className="thread-item__left">
                <div className="thread-item__read-indicator" data-read={thread.is_read} />
                <div className="thread-item__thread-details">
                    <div className="thread-item__sender-info">
                        <ThreadItemRecipients recipients={thread.recipients} />
                        <div className="thread-item__metadata">
                            {/* {thread.has_attachments ? (
                                <span className="thread-item__metadata-attachments">
                                    <Tooltip placement="bottom" content={t('tooltips.has_attachments')}>
                                        <span className="material-icons">attachment</span>
                                    </Tooltip>
                                </span>
                            ) : null} */}
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
                <span className="thread-item__date">
                    {DateHelper.formatDate(thread.updated_at, i18n.language)}
                </span>
            </div>
        </Link>
    )
}
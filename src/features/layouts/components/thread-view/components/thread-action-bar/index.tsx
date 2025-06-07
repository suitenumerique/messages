import { useTranslation } from "react-i18next"
import { Button } from "@openfun/cunningham-react"
import { Tooltip } from "@gouvfr-lasuite/ui-kit"
import { useMailboxContext } from "@/features/providers/mailbox"
import { ThreadAccessesWidget } from "../thread-accesses-widget"
import { useRead } from "@/features/message/use-read"
import { useTrash } from "@/features/message/use-trash"
import { ThreadAccessRoleEnum } from "@/features/api/gen/models"
import { LabelBadge } from "@/features/layouts/components/label-badge"

export const ActionBar = () => {
    const { t } = useTranslation()
    const { selectedThread, unselectThread } = useMailboxContext()
    const { markAsUnread } = useRead()
    const { markAsTrashed, markAsUntrashed } = useTrash()

    if (!selectedThread) return null

    const hasOnlyOneEditor = selectedThread.accesses.filter(
        (access) => access.role === ThreadAccessRoleEnum.editor
    ).length === 1

    return (
        <div className="thread-action-bar">
            <div className="thread-action-bar__left">
                {selectedThread.labels && selectedThread.labels.length > 0 && (
                    <div className="thread-action-bar__labels">
                        {selectedThread.labels.map((label) => (
                            <LabelBadge key={label.id} label={label} />
                        ))}
                    </div>
                )}
            </div>
            <div className="thread-action-bar__right">
                <ThreadAccessesWidget accesses={selectedThread.accesses} />
                <Tooltip content={t('actions.mark_as_unread')}>
                    <Button
                        color="primary-text"
                        aria-label={t('actions.mark_as_unread')}
                        size="small"
                        icon={<span className="material-icons">mark_email_unread</span>}
                        onClick={() => markAsUnread({ threadIds: [selectedThread.id], onSuccess: unselectThread })}
                    />
                </Tooltip>
                {
                    selectedThread.count_trashed < selectedThread.count_messages ? (
                        <Tooltip content={t('actions.delete')}>
                            <Button
                                color="primary-text"
                                aria-label={t('actions.delete')}
                                size="small"
                                icon={<span className="material-icons">delete</span>}
                                onClick={() => markAsTrashed({ threadIds: [selectedThread.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    ) : (
                        <Tooltip content={t('actions.undelete')}>
                            <Button
                                color="primary-text"
                                aria-label={t('actions.undelete')}
                                size="small"
                                icon={<span className="material-icons">restore</span>}
                                onClick={() => markAsUntrashed({ threadIds: [selectedThread.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                }
            </div>
        </div>
    )
} 
import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import Bar from "@/features/ui/components/bar";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget } from "../thread-accesses-widget";

export const ActionBar = () => {
    const { t } = useTranslation();
    const { selectedThread, unselectThread } = useMailboxContext();
    const { markAsUnread } = useRead();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);

    return (
        <Bar className="thread-action-bar">
            <div className="thread-action-bar__left">
            <Tooltip content={t('actions.close_thread')}>
                <Button
                    onClick={unselectThread}
                    color="tertiary-text"
                    aria-label={t('tooltips.close_thread')}
                    size="small"
                    icon={<span className="material-icons">close</span>}
                />
                </Tooltip>
            </div>
            <div className="thread-action-bar__right">
                <ThreadAccessesWidget accesses={selectedThread!.accesses} />
                <Tooltip content={t('actions.mark_as_unread')}>
                    <Button
                        color="primary-text"
                        aria-label={t('actions.mark_as_unread')}
                        size="small"
                        icon={<span className="material-icons">mark_email_unread</span>}
                        onClick={() => markAsUnread({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                    />
                </Tooltip>
                {
                    selectedThread!.count_trashed < selectedThread!.count_messages ? (
                        <Tooltip content={t('actions.delete')}>
                            <Button
                                color="primary-text"
                                aria-label={t('actions.delete')}
                                size="small"
                                icon={<span className="material-icons">delete</span>}
                                onClick={() => markAsTrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    ) : (
                        <Tooltip content={t('actions.undelete')}>
                            <Button
                                color="primary-text"
                                aria-label={t('actions.undelete')}
                                size="small"
                                icon={<span className="material-icons">restore</span>}
                                onClick={() => markAsUntrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                }
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        {
                            label: t('actions.print'),
                            icon: <span className="material-icons">print</span>,
                        },
                    ]}
                >
                    <Tooltip content={t('tooltips.more_options')}>
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<span className="material-icons">more_vert</span>}
                            color="primary-text"
                            aria-label={t('tooltips.more_options')}
                            size="small"
                        />
                    </Tooltip>
                </DropdownMenu>
            </div>
        </Bar>
    )
}

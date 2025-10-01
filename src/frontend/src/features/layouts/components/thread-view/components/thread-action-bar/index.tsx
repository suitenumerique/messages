import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import Bar from "@/features/ui/components/bar";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget } from "../thread-accesses-widget";
import { ThreadLabelsWidget } from "../thread-labels-widget";

type ActionBarProps = {
    canUndelete: boolean;
}

export const ActionBar = ({ canUndelete }: ActionBarProps) => {
    const { t } = useTranslation();
    const { selectedThread, unselectThread } = useMailboxContext();
    const { markAsUnread } = useRead();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);

    return (
        <Bar className="thread-action-bar">
            <div className="thread-action-bar__left">
                <Tooltip content={t('Close this thread')}>
                    <Button
                        onClick={unselectThread}
                        color="tertiary-text"
                        aria-label={t('Close this thread')}
                        size="small"
                        icon={<span className="material-icons">close</span>}
                    />
                </Tooltip>
            </div>
            <div className="thread-action-bar__right">
                <ThreadAccessesWidget accesses={selectedThread!.accesses} />
                <ThreadLabelsWidget threadId={selectedThread!.id} selectedLabels={selectedThread!.labels} />
                <Tooltip content={t('Mark as unread')}>
                    <Button
                        color="primary-text"
                        aria-label={t('Mark as unread')}
                        size="small"
                        icon={<span className="material-icons">mark_email_unread</span>}
                        onClick={() => markAsUnread({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                    />
                </Tooltip>
                {
                    selectedThread!.has_trashed ? (
                        canUndelete && (
                            <Tooltip content={t('Undelete')}>
                                <Button
                                    color="primary-text"
                                    aria-label={t('Undelete')}
                                    size="small"
                                    icon={<span className="material-icons">restore_from_trash</span>}
                                    onClick={() => markAsUntrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                                />
                            </Tooltip>
                        )
                    ) : (
                        <Tooltip content={t('Delete')}>
                            <Button
                                color="primary-text"
                                aria-label={t('Delete')}
                                size="small"
                                icon={<span className="material-icons">delete</span>}
                                onClick={() => markAsTrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                }
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        {
                            label: t('Print'),
                            icon: <span className="material-icons">print</span>,
                        },
                    ]}
                >
                    <Tooltip content={t('More options')}>
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<span className="material-icons">more_vert</span>}
                            color="primary-text"
                            aria-label={t('More options')}
                            size="small"
                        />
                    </Tooltip>
                </DropdownMenu>
            </div>
        </Bar>
    )
}

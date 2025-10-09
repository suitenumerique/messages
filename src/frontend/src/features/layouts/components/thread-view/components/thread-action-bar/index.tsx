import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import Bar from "@/features/ui/components/bar";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget } from "../thread-accesses-widget";
import { ThreadLabelsWidget } from "../thread-labels-widget";
import useArchive from "@/features/message/use-archive";

type ActionBarProps = {
    canUndelete: boolean;
    canUnarchive: boolean;
}

export const ActionBar = ({ canUndelete, canUnarchive }: ActionBarProps) => {
    const { t } = useTranslation();
    const { selectedThread, unselectThread } = useMailboxContext();
    const { markAsUnread } = useRead();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { markAsArchived, markAsUnarchived } = useArchive();
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
                        icon={<Icon name="close" />}
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
                        icon={<Icon name="mark_email_unread" type={IconType.OUTLINED} />}
                        onClick={() => markAsUnread({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                    />
                </Tooltip>
                {
                    canUnarchive ? (
                        (
                            <Tooltip content={t('Unarchive')}>
                                <Button
                                    color="primary-text"
                                    aria-label={t('Unarchive')}
                                    size="small"
                                    icon={<Icon name="unarchive" type={IconType.OUTLINED} />}
                                    onClick={() => markAsUnarchived({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                                />
                            </Tooltip>
                        )
                    ) : (
                        <Tooltip content={t('Archive')}>
                            <Button
                                color="primary-text"
                                aria-label={t('Archive')}
                                size="small"
                                icon={<Icon name="archive" type={IconType.OUTLINED} />}
                                onClick={() => markAsArchived({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                }
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        canUndelete ? {
                            label: t('Undelete'),
                            icon: <Icon name="restore_from_trash" type={IconType.OUTLINED} />,
                            callback: () => markAsUntrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread }),
                        } : {
                            label: t('Delete'),
                            icon: <Icon name="delete" type={IconType.OUTLINED} />,
                            callback: () => markAsTrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread }),
                        }
                    ]}
                >
                    <Tooltip content={t('More options')}>
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<Icon name="more_vert" type={IconType.OUTLINED} />}
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

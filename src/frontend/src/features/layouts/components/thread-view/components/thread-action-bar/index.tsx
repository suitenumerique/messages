import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import { DropdownMenu, Icon, IconType, VerticalSeparator } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget } from "../thread-accesses-widget";
import { ThreadLabelsWidget } from "../thread-labels-widget";
import useArchive from "@/features/message/use-archive";
import useSpam from "@/features/message/use-spam";
import useStarred from "@/features/message/use-starred";

type ThreadActionBarProps = {
    canUndelete: boolean;
    canUnarchive: boolean;
}

export const ThreadActionBar = ({ canUndelete, canUnarchive }: ThreadActionBarProps) => {
    const { t } = useTranslation();
    const { selectedThread, unselectThread } = useMailboxContext();
    const { markAsReadAt } = useRead();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsSpam, markAsNotSpam } = useSpam();
    const { markAsStarred, markAsUnstarred } = useStarred();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const isStarred = selectedThread?.has_starred;

    return (
        <div className="thread-action-bar">
            <Tooltip content={t('Close this thread')} placement="left">
                <Button
                    onClick={unselectThread}
                    variant="tertiary"
                    aria-label={t('Close this thread')}
                    size="nano"
                    icon={<Icon name="close" />}
                />
            </Tooltip>
            <VerticalSeparator />
            {!selectedThread?.is_spam && (
                canUnarchive ? (
                    (
                        <Tooltip content={t('Unarchive')}>
                            <Button
                                variant="tertiary"
                                aria-label={t('Unarchive')}
                                size="nano"
                                icon={<Icon name="unarchive" type={IconType.OUTLINED} />}
                                onClick={() => markAsUnarchived({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                ) : (
                    <Tooltip content={t('Archive')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Archive')}
                            size="nano"
                            icon={<Icon name="archive" type={IconType.OUTLINED} />}
                            onClick={() => markAsArchived({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                        />
                    </Tooltip>
                )
            )}
            {
                !selectedThread?.is_spam ? (
                    <Tooltip content={t('Report as spam')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Report as spam')}
                            size="nano"
                            icon={<Icon name="report" type={IconType.OUTLINED} />}
                            onClick={() => markAsSpam({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                        />
                    </Tooltip>
                ) : (
                    <Tooltip content={t('Remove spam report')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Remove spam report')}
                            size="nano"
                            icon={<Icon name="report_off" type={IconType.OUTLINED} />}
                            onClick={() => markAsNotSpam({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                        />
                    </Tooltip>
                )
            }
            {
                canUndelete ? (
                    (
                        <Tooltip content={t('Undelete')}>
                            <Button
                                variant="tertiary"
                                aria-label={t('Undelete')}
                                size="nano"
                                icon={<Icon name="restore_from_trash" type={IconType.OUTLINED} />}
                                onClick={() => markAsUntrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                ) : (
                    <Tooltip content={t('Delete')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Delete')}
                            size="nano"
                            icon={<Icon name="delete" type={IconType.OUTLINED} />}
                            onClick={() => markAsTrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                        />
                    </Tooltip>
                )
            }
            <VerticalSeparator />
            {isStarred ? (
                <Tooltip content={t('Unstar this thread')}>
                    <Button
                        variant="tertiary"
                        aria-label={t('Unstar this thread')}
                        size="nano"
                        icon={<Icon name="star" />}
                        onClick={() => markAsUnstarred({ threadIds: [selectedThread!.id] })}
                    />
                </Tooltip>
            ) : (
                <Tooltip content={t('Star this thread')}>
                    <Button
                        variant="tertiary"
                        aria-label={t('Star this thread')}
                        size="nano"
                        icon={<Icon name="star_border" />}
                        onClick={() => markAsStarred({ threadIds: [selectedThread!.id] })}
                    />
                </Tooltip>
            )}
            <ThreadLabelsWidget threadId={selectedThread!.id} selectedLabels={selectedThread!.labels} />
            <ThreadAccessesWidget accesses={selectedThread!.accesses} />
            <DropdownMenu
                isOpen={isDropdownOpen}
                onOpenChange={setIsDropdownOpen}
                options={[
                    {
                        label: t('Mark as unread'),
                        icon: <Icon name="mark_email_unread" type={IconType.OUTLINED} />,
                        callback: () => markAsReadAt({ threadIds: [selectedThread!.id], readAt: null, onSuccess: unselectThread })
                    }
                ]}
            >
                <Tooltip content={t('More options')}>
                    <Button
                        onClick={() => setIsDropdownOpen(true)}
                        icon={<Icon name="more_vert" type={IconType.OUTLINED} />}
                        variant="tertiary"
                        aria-label={t('More options')}
                        size="nano"
                    />
                </Tooltip>
            </DropdownMenu>
        </div>
    )
}

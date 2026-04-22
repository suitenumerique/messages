import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { DropdownMenu, Icon, IconType, VerticalSeparator } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip, useModals } from "@gouvfr-lasuite/cunningham-react"
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget, type ThreadAccessesWidgetHandle } from "../thread-accesses-widget";
import { AssigneesWidget } from "../assignees-widget";
import { LabelsWidget } from "@/features/layouts/components/labels-widget";
import useArchive from "@/features/message/use-archive";
import useSpam from "@/features/message/use-spam";
import useStarred from "@/features/message/use-starred";
import { MailboxRoleChoices, ThreadAccessRoleChoices, useThreadsAccessesDestroy } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";

type ThreadActionBarProps = {
    canUndelete: boolean;
    canUnarchive: boolean;
}

export const ThreadActionBar = ({ canUndelete, canUnarchive }: ThreadActionBarProps) => {
    const { t } = useTranslation();
    const { selectedMailbox, selectedThread, unselectThread, invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
    const { markAsReadAt } = useRead();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsSpam, markAsNotSpam } = useSpam();
    const { markAsStarred, markAsUnstarred } = useStarred();
    const { mutate: removeThreadAccess } = useThreadsAccessesDestroy();
    const modals = useModals();
    const accessesWidgetRef = useRef<ThreadAccessesWidgetHandle>(null);
    // Full edit rights on the thread — gates archive, spam, delete.
    // Star and "mark as unread" remain visible because they are personal
    // state on the user's ThreadAccess (read_at / starred_at).
    // Label assignment is scoped to the mailbox (see `LabelsWidget`) and
    // therefore stays visible for viewer-only threads.
    const canEditThread = useAbility(Abilities.CAN_EDIT_THREAD, selectedThread ?? null);
    const canShowArchiveCTA = canEditThread && !selectedThread?.is_spam
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const isStarred = selectedThread?.has_starred;
    const mailboxAccess = selectedThread?.accesses.find((a) => a.mailbox.id === selectedMailbox?.id);
    const hasOnlyOneEditor = selectedThread?.accesses.filter((a) => a.role === ThreadAccessRoleChoices.editor).length === 1;
    const canLeaveThread = selectedMailbox?.role !== MailboxRoleChoices.viewer && mailboxAccess && selectedThread && (!hasOnlyOneEditor || mailboxAccess.role !== ThreadAccessRoleChoices.editor);

    const handleLeaveThread = async () => {
        if (!mailboxAccess || !selectedThread) return;
        const decision = await modals.deleteConfirmationModal({
            title: t('Leave this thread?'),
            children: t(
                'You and all users with access to the mailbox \"{{mailboxName}}\" will no longer see this thread.',
                { mailboxName: mailboxAccess.mailbox.email }
            ),
        });
        if (decision !== 'delete') return;
        removeThreadAccess({
            id: mailboxAccess.id,
            threadId: selectedThread.id,
        }, {
            onSuccess: () => {
                addToast(<ToasterItem><p>{t('You left the thread')}</p></ToasterItem>);
                invalidateThreadMessages({
                    type: 'delete',
                    metadata: { threadIds: [selectedThread.id] },
                });
                invalidateThreadsStats();
                unselectThread();
            }
        });
    };

  return (
    <div className="thread-action-bar__container">
      <div className="thread-action-bar">
        <AssigneesWidget onClick={() => accessesWidgetRef.current?.open()} />
      </div>
        <div className="thread-action-bar">
            <Tooltip content={t('Close this thread')} placement="bottom">
                <Button
                    onClick={unselectThread}
                    variant="tertiary"
                    aria-label={t('Close this thread')}
                    size="nano"
                    icon={<Icon name="close" />}
                />
            </Tooltip>
            <VerticalSeparator />
            {canShowArchiveCTA && (
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
            {canEditThread && (
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
            )}
            {canEditThread && (
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
            )}
            {canEditThread && <VerticalSeparator />}
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
            <LabelsWidget threadIds={[selectedThread!.id]} initialLabels={selectedThread!.labels} />
            <ThreadAccessesWidget ref={accessesWidgetRef} accesses={selectedThread!.accesses} />
            <DropdownMenu
                isOpen={isDropdownOpen}
                onOpenChange={setIsDropdownOpen}
                options={[
                    {
                        label: t('Mark as unread'),
                        icon: <Icon name="mark_email_unread" type={IconType.OUTLINED} />,
                        callback: () => markAsReadAt({ threadIds: [selectedThread!.id], readAt: null, onSuccess: unselectThread })
                    },
                    ...(canLeaveThread ? [{
                        label: t('Leave this thread'),
                        icon: <Icon name="exit_to_app" type={IconType.OUTLINED} />,
                        callback: handleLeaveThread,
                    }] : []),
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
      </div>
    )
}

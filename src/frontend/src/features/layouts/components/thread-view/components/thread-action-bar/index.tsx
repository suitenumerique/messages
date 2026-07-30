import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { DropdownMenu, IconType, VerticalSeparator } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget, type ThreadAccessesWidgetHandle } from "../thread-accesses-widget";
import { AssigneesWidget } from "../assignees-widget";
import { LabelsWidget } from "@/features/layouts/components/labels-widget";
import useArchive from "@/features/message/use-archive";
import useSpam from "@/features/message/use-spam";
import useDeleteDrafts from "@/features/message/use-delete-drafts";
import useLeaveThread from "@/features/message/use-leave-thread";
import ViewHelper from "@/features/utils/view-helper";
import useCopyDeepLink from "@/features/message/use-copy-deep-link";
import { Icon } from "@/features/ui/components/icon";
import { Archive, Link, MoreVertical, Trash } from "@gouvfr-lasuite/ui-kit/icons";

type ThreadActionBarProps = {
    canUndelete: boolean;
    canUnarchive: boolean;
}

export const ThreadActionBar = ({ canUndelete, canUnarchive }: ThreadActionBarProps) => {
    const { t } = useTranslation();
    const { selectedThread, unselectThread, messages } = useMailboxContext();
    const { markAsReadAt } = useRead();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsSpam, markAsNotSpam } = useSpam();
    const { deleteDrafts } = useDeleteDrafts();
    const { canLeaveThread, leaveThread } = useLeaveThread();
    const isDraftsView = ViewHelper.isDraftsView();
    const accessesWidgetRef = useRef<ThreadAccessesWidgetHandle>(null);
    // Full edit rights on the thread — gates archive, spam, delete.
    // Star and read/unread toggle remain visible because they are personal
    // state on the user's ThreadAccess (read_at / starred_at).
    // Label assignment is scoped to the mailbox (see `LabelsWidget`) and
    // therefore stays visible for viewer-only threads.
    const canEditThread = useAbility(Abilities.CAN_EDIT_THREAD, selectedThread ?? null);
    // Archiving, reporting as spam or labelling makes no sense from the
    // trash view (or on a fully trashed thread opened elsewhere): the only
    // relevant transition from the trash is restoring it (Undelete).
    const isTrashContext = ViewHelper.isTrashedView() || canUndelete;
    const canShowArchiveCTA = canEditThread && !selectedThread?.is_spam && !isTrashContext
    const canShowSpamCTA = canEditThread && !isTrashContext
    // A thread holding nothing but the draft being composed: archiving, spam
    // reporting or trashing it is meaningless — only permanent deletion is. When
    // the draft replies inside a real thread, those actions still make sense but
    // are demoted to the "More options" menu (see the dropdown below).
    const isDraftOnlyThread = isDraftsView && !!messages && messages.length > 0 && messages.every((m) => m.is_draft);
    const showDraftThreadManagement = isDraftsView && !isDraftOnlyThread && canEditThread;
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const hasUnread = selectedThread?.has_unread;
    const copyDeepLink = useCopyDeepLink();

    return (
        <div className="thread-action-bar__container">
            <div className="thread-action-bar">
                <AssigneesWidget onClick={() => accessesWidgetRef.current?.open()} />
            </div>
            <div className="thread-action-bar">
                {canEditThread && !isDraftsView && (
                    canUndelete ? (
                        <Tooltip content={t('Undelete')}>
                            <Button
                                variant="tertiary"
                                aria-label={t('Undelete')}
                                size="nano"
                                icon={<Icon name="restore_from_trash" type={IconType.OUTLINED} />}
                                onClick={() => markAsUntrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    ) : (
                        <Tooltip content={t('Delete')}>
                            <Button
                                variant="tertiary"
                                aria-label={t('Delete')}
                                size="nano"
                                icon={<Icon icon={Trash} />}
                                onClick={() => markAsTrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                )}
                {canEditThread && isDraftsView && (
                    <Tooltip content={t('Delete draft')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Delete draft')}
                            size="nano"
                            icon={<Icon name="edit_off" type={IconType.OUTLINED} />}
                            onClick={() => deleteDrafts({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                        />
                    </Tooltip>
                )}
                {canShowArchiveCTA && !isDraftsView && (
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
                                icon={<Icon icon={Archive} />}
                                onClick={() => markAsArchived({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    )
                )}
                {canShowSpamCTA && !isDraftsView && (
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
                {canEditThread && <VerticalSeparator />}
                {hasUnread ? (
                    <Tooltip content={t('Mark as read')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Mark as read')}
                            size="nano"
                            icon={<Icon  name="mail-open" />}
                            onClick={() => markAsReadAt({ threadIds: [selectedThread!.id], readAt: new Date().toISOString() })}
                        />
                    </Tooltip>
                ) : (
                    <Tooltip content={t('Mark as unread')}>
                        <Button
                            variant="tertiary"
                            aria-label={t('Mark as unread')}
                            size="nano"
                            icon={<Icon name="mail-unread" />}
                            onClick={() => {
                                unselectThread();
                                markAsReadAt({ threadIds: [selectedThread!.id], readAt: null });
                            }}
                        />
                    </Tooltip>
                )}
                {!isTrashContext && <LabelsWidget threadIds={[selectedThread!.id]} initialLabels={selectedThread!.labels} />}
                <ThreadAccessesWidget ref={accessesWidgetRef} accesses={selectedThread!.accesses} />
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        ...(showDraftThreadManagement ? [
                            {
                                label: t('Archive'),
                                icon: <Icon icon={Archive} />,
                                callback: () => markAsArchived({ threadIds: [selectedThread!.id], onSuccess: unselectThread }),
                            },
                            {
                                label: t('Report as spam'),
                                icon: <Icon name="report" type={IconType.OUTLINED} />,
                                callback: () => markAsSpam({ threadIds: [selectedThread!.id], onSuccess: unselectThread }),
                            },
                            {
                                label: t('Move to trash'),
                                icon: <Icon icon={Trash} />,
                                callback: () => markAsTrashed({ threadIds: [selectedThread!.id], onSuccess: unselectThread }),
                                showSeparator: true,
                            },
                        ] : []),
                        {
                            label: t('Copy link to thread'),
                            icon: <Icon icon={Link} />,
                            callback: () => copyDeepLink(),
                        },
                        ...(canLeaveThread ? [{
                            label: t('Leave this thread'),
                            icon: <Icon name="exit_to_app" type={IconType.OUTLINED} />,
                            callback: leaveThread,
                        }] : []),
                    ]}
                >
                    <Tooltip content={t('More options')}>
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<Icon icon={MoreVertical} />}
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

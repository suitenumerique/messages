import { useMailboxContext } from "@/features/providers/mailbox";
import useRead from "@/features/message/use-read";
import useTrash from "@/features/message/use-trash";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { DropdownMenu, IconType, VerticalSeparator, useResponsive } from "@gouvfr-lasuite/ui-components"
import { Button, Spinner, Tooltip } from "@gouvfr-lasuite/ui-components"
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ThreadAccessesWidget, type ThreadAccessesWidgetHandle } from "../thread-accesses-widget";
import { AssigneesWidget } from "../assignees-widget";
import { LabelsWidget } from "@/features/layouts/components/labels-widget";
import useArchive from "@/features/message/use-archive";
import useSpam from "@/features/message/use-spam";
import useThreadRemoveActions from "@/features/message/use-thread-remove-actions";
import useLeaveThread from "@/features/message/use-leave-thread";
import ViewHelper from "@/features/utils/view-helper";
import useCopyDeepLink from "@/features/message/use-copy-deep-link";
import { Icon } from "@/features/ui/components/icon";
import { Archive, Link, MoreVertical, Trash, Error as ErrorIcon, XMark } from "@gouvfr-lasuite/ui-components/icons";

type ThreadActionBarProps = {
    /** Trash view, or a fully trashed thread opened from any other view. */
    isTrashContext: boolean;
    canUnarchive: boolean;
}

export const ThreadActionBar = ({ isTrashContext, canUnarchive }: ThreadActionBarProps) => {
    const { t } = useTranslation();
    const { selectedThread, unselectThread, messages } = useMailboxContext();
    const { isMobile } = useResponsive();
    const { markAsReadAt } = useRead();
    const { markAsTrashed } = useTrash();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsSpam, markAsNotSpam } = useSpam();
    const { canLeaveThread, leaveThread } = useLeaveThread();
    const isDraftsView = ViewHelper.isDraftsView();
    const removeActions = useThreadRemoveActions(selectedThread, isTrashContext);
    const accessesWidgetRef = useRef<ThreadAccessesWidgetHandle>(null);
    // Full edit rights on the thread — gates archive, spam, delete.
    // Star and read/unread toggle remain visible because they are personal
    // state on the user's ThreadAccess (read_at / starred_at).
    // Label assignment is scoped to the mailbox (see `LabelsWidget`) and
    // therefore stays visible for viewer-only threads.
    const canEditThread = useAbility(Abilities.CAN_EDIT_THREAD, selectedThread ?? null);
    // Archiving, reporting as spam or labelling makes no sense from the
    // trash context: the only relevant transitions are restoring or deleting.
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
                { isMobile &&
                    <>
                        <Tooltip content={t('Close this thread')} placement="bottom">
                            <Button
                                onClick={unselectThread}
                                variant="tertiary"
                                aria-label={t('Close this thread')}
                                size="nano"
                                icon={<Icon icon={XMark} />}
                            />
                        </Tooltip>
                        <VerticalSeparator withPadding={false} />
                    </>
                }
                {removeActions.map((action) => (
                    <Tooltip key={action.key} content={action.label}>
                        <Button
                            variant="tertiary"
                            aria-label={action.label}
                            size="nano"
                            icon={action.isPending ? <Spinner size="sm" /> : <Icon {...action.icon} />}
                            disabled={action.isPending}
                            onClick={action.onSelect}
                        />
                    </Tooltip>
                ))}
                {canShowArchiveCTA && !isDraftsView && (
                    canUnarchive ? (
                        (
                            <Tooltip content={t('Unarchive')}>
                                <Button
                                    variant="tertiary"
                                    aria-label={t('Unarchive')}
                                    size="nano"
                                    icon={<Icon name="inbox" />}
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
                                icon={<Icon icon={ErrorIcon} />}
                                onClick={() => markAsSpam({ threadIds: [selectedThread!.id], onSuccess: unselectThread })}
                            />
                        </Tooltip>
                    ) : (
                        <Tooltip content={t('Remove spam report')}>
                            <Button
                                variant="tertiary"
                                aria-label={t('Remove spam report')}
                                size="nano"
                                icon={<Icon name="error-off" />}
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
                                icon: <Icon icon={ErrorIcon} />,
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

import { useSearchParams } from "next/navigation";
import { MAILBOX_FOLDERS } from "../../mailbox-panel/components/mailbox-list";
import { useLabelsList } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { useMemo, useState } from "react";
import { Button, Tooltip, Checkbox } from "@gouvfr-lasuite/cunningham-react";
import useRead from "@/features/message/use-read";
import { DropdownMenu, Icon, IconType, VerticalSeparator } from "@gouvfr-lasuite/ui-kit";
import ViewHelper from "@/features/utils/view-helper";
import useArchive from "@/features/message/use-archive";
import useSpam from "@/features/message/use-spam";
import useTrash from "@/features/message/use-trash";
import useStarred from "@/features/message/use-starred";

type ThreadPanelTitleProps = {
    selectedThreadIds: Set<string>;
    isAllSelected: boolean;
    isSomeSelected: boolean;
    isSelectionMode: boolean;
    onSelectAll: () => void;
    onClearSelection: () => void;
    onEnableSelectionMode: () => void;
    onDisableSelectionMode: () => void;
}

const ThreadPanelTitle = ({ selectedThreadIds, isAllSelected, isSomeSelected, isSelectionMode, onSelectAll, onClearSelection, onEnableSelectionMode, onDisableSelectionMode }: ThreadPanelTitleProps) => {
    const { t } = useTranslation();
    const { markAsReadAt } = useRead();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { markAsSpam, markAsNotSpam } = useSpam();
    const { markAsStarred, markAsUnstarred } = useStarred();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const searchParams = useSearchParams();
    const isSearch = searchParams.has('search');
    const { threads, selectedMailbox, unselectThread } = useMailboxContext();
    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox?.id }, { query: { enabled: !!selectedMailbox && !!searchParams.get('label_slug') } })
    const isTrashedView = ViewHelper.isTrashedView();
    const isSpamView = ViewHelper.isSpamView();
    const isArchivedView = ViewHelper.isArchivedView();
    const isSentView = ViewHelper.isSentView();
    const isDraftsView = ViewHelper.isDraftsView();

    const title = useMemo(() => {
        if (searchParams.has('search')) return t('folder.search', { defaultValue: 'Search' });
        if (searchParams.has('label_slug')) return (labelsQuery.data?.data || []).find((label) => label.slug === searchParams.get('label_slug'))?.name;
        return MAILBOX_FOLDERS().find((folder) => new URLSearchParams(folder.filter).toString() === searchParams.toString())?.name;
    }, [searchParams, labelsQuery.data?.data, selectedMailbox, t])

    const handleSelectAllToggle = () => {
        if (isAllSelected) {
            onClearSelection();
        } else {
            onSelectAll();
        }
    };

    const threadIdsToMark = useMemo(() => {
        if (selectedThreadIds.size > 0) {
            return Array.from(selectedThreadIds);
        }
        return threads?.results.map((thread) => thread.id) || [];
    }, [selectedThreadIds, threads?.results]);

    const markAllTooltip = selectedThreadIds.size > 0
        ? t('Mark {{count}} threads as read', { count: selectedThreadIds.size, defaultValue_one: 'Mark {{count}} thread as read' })
        : t('Mark all as read');

    const markAllUnreadLabel = selectedThreadIds.size > 0
        ? t('Mark {{count}} threads as unread', { count: selectedThreadIds.size, defaultValue_one: 'Mark {{count}} thread as unread' })
        : t('Mark all as unread');

    const spamLabel = isSpamView ?
        t('Remove spam report from {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Remove spam report from {{count}} thread' }) :
        t('Report {{count}} threads as spam', { count: selectedThreadIds.size, defaultValue_one: 'Report {{count}} thread as spam' });
    const spamIconName = isSpamView ? 'report_off' : 'report';
    const spamMutation = isSpamView ? markAsNotSpam : markAsSpam;

    const archiveLabel = isArchivedView ?
        t('Unarchive {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Unarchive {{count}} thread' }) :
        t('Archive {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Archive {{count}} thread' });
    const archiveIconName = isArchivedView ? 'unarchive' : 'archive';
    const archiveMutation = isArchivedView ? markAsUnarchived : markAsArchived;

    const trashLabel = isTrashedView ?
        t('Undelete {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Undelete {{count}} thread' }) :
        t('Delete {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Delete {{count}} thread' });
    const trashIconName = isTrashedView ? 'restore_from_trash' : 'delete';
    const trashMutation = isTrashedView ? markAsUntrashed : markAsTrashed;

    const starLabel = t('Star {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Star {{count}} thread' });
    const unstarLabel = t('Unstar {{count}} threads', { count: selectedThreadIds.size, defaultValue_one: 'Unstar {{count}} thread' });

    return (
        <header className="thread-panel__header">
            <h2 className="thread-panel__header--title">{title}</h2>
            <div className="thread-panel__header--details">
                {(isSelectionMode || isSomeSelected) && (
                    <Checkbox
                        checked={isAllSelected}
                        indeterminate={isSomeSelected && !isAllSelected}
                        onChange={handleSelectAllToggle}
                        aria-label={isAllSelected ? t('Deselect all threads') : t('Select all threads')}
                        className="thread-panel__header--checkbox"
                    />
                )}
                <p className="thread-panel__header--count">
                    {isSearch
                        ? t('{{count}} results', { count: threads?.count, defaultValue_one: '{{count}} result' })
                        : t('{{count}} messages', { count: threads?.count, defaultValue_one: '{{count}} message' })
                    }
                </p>
                <div className="thread-panel__bar">
                    <Tooltip content={markAllTooltip}>
                        <Button
                            onClick={() => {
                                markAsReadAt({
                                    threadIds: threadIdsToMark,
                                    readAt: new Date().toISOString(),
                                    onSuccess: () => {
                                        unselectThread();
                                        onClearSelection();
                                    }
                                });
                            }}
                            icon={<Icon name="mark_email_read" type={IconType.OUTLINED} />}
                            variant="tertiary"
                            size="nano"
                            aria-label={markAllTooltip}
                        />
                    </Tooltip>
                    {isSelectionMode && (
                        <>
                            <Tooltip content={starLabel} className={selectedThreadIds.size === 0 ? 'hidden' : ''}>
                                <Button
                                    onClick={() => {
                                        markAsStarred({
                                            threadIds: threadIdsToMark,
                                            onSuccess: () => {
                                                unselectThread();
                                                onClearSelection();
                                            }
                                        });
                                    }}
                                    disabled={selectedThreadIds.size === 0}
                                    icon={<Icon name="star_border" type={IconType.OUTLINED} />}
                                    variant="tertiary"
                                    size="nano"
                                    aria-label={starLabel}
                                />
                            </Tooltip>
                            <VerticalSeparator withPadding={false} />
                            {!isSpamView && !isTrashedView && !isDraftsView && (
                                <Tooltip content={archiveLabel} className={selectedThreadIds.size === 0 ? 'hidden' : ''}>
                                    <Button
                                        onClick={() => {
                                            archiveMutation({
                                                threadIds: threadIdsToMark,
                                                onSuccess: () => {
                                                    unselectThread();
                                                    onClearSelection();
                                                }
                                            });
                                        }}
                                        disabled={selectedThreadIds.size === 0}
                                        icon={<Icon name={archiveIconName} type={IconType.OUTLINED} />}
                                        variant="tertiary"
                                        size="nano"
                                        aria-label={archiveLabel}
                                    />
                                </Tooltip>
                            )}
                            {!isTrashedView && !isSentView && !isDraftsView && (
                                <Tooltip content={spamLabel} className={selectedThreadIds.size === 0 ? 'hidden' : ''}>
                                    <Button
                                        onClick={() => {
                                            spamMutation({
                                                threadIds: threadIdsToMark,
                                                onSuccess: () => {
                                                    unselectThread();
                                                    onClearSelection();
                                                }
                                            });
                                        }}
                                        disabled={selectedThreadIds.size === 0}
                                        icon={<Icon name={spamIconName} type={IconType.OUTLINED} />}
                                        variant="tertiary"
                                        size="nano"
                                        aria-label={spamLabel}
                                    />
                                </Tooltip>
                            )}
                            {
                                !isDraftsView && (
                                    <Tooltip content={trashLabel} className={selectedThreadIds.size === 0 ? 'hidden' : ''}>
                                        <Button
                                            onClick={() => {
                                                trashMutation({
                                                    threadIds: threadIdsToMark,
                                                    onSuccess: () => {
                                                        unselectThread();
                                                        onClearSelection();
                                                    }
                                                });
                                            }}
                                            disabled={selectedThreadIds.size === 0}
                                            icon={<Icon name={trashIconName} type={IconType.OUTLINED} />}
                                            variant="tertiary"
                                            size="nano"
                                            aria-label={trashLabel}
                                        />
                                    </Tooltip>
                                )
                            }
                            <VerticalSeparator withPadding={false} />
                        </>
                    )}
                    <DropdownMenu
                        isOpen={isDropdownOpen}
                        onOpenChange={setIsDropdownOpen}
                        options={[
                            {
                                label: isSelectionMode ? t('Disable thread selection') : t('Select threads'),
                                icon: <Icon name="checklist" />,
                                callback: () => {
                                    if (isSelectionMode) {
                                        onDisableSelectionMode();
                                    } else {
                                        onEnableSelectionMode();
                                    }
                                },
                                showSeparator: true,
                            },
                            {
                                label: markAllUnreadLabel,
                                icon: <span className="material-icons">mark_email_unread</span>,
                                callback: () => {
                                    markAsReadAt({
                                        threadIds: threadIdsToMark,
                                        readAt: null,
                                        onSuccess: () => {
                                            unselectThread();
                                            onClearSelection();
                                        }
                                    })
                                },
                            },
                            ...(isSelectionMode && selectedThreadIds.size > 0 ? [{
                                label: unstarLabel,
                                icon: <Icon name="star" type={IconType.FILLED} />,
                                callback: () => {
                                    markAsUnstarred({
                                        threadIds: threadIdsToMark,
                                        onSuccess: () => {
                                            unselectThread();
                                            onClearSelection();
                                        }
                                    });
                                },
                            }] : []),
                        ]}
                    >
                        <Tooltip content={t('More options')}>
                            <Button
                                onClick={() => setIsDropdownOpen(true)}
                                icon={<span className="material-icons">more_vert</span>}
                                variant="tertiary"
                                aria-label={t('More options')}
                                size="nano"
                            />
                        </Tooltip>
                    </DropdownMenu>
                </div>
            </div>
        </header>
    )
}

export default ThreadPanelTitle;

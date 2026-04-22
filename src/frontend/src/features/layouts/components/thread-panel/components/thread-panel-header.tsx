import { useSearchParams } from "next/navigation";
import { findRootFolder } from "../../mailbox-panel/components/mailbox-list";
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
import useCanEditThreads from "@/features/message/use-can-edit-threads";
import { ThreadPanelFilter } from "./thread-panel-filter";
import { THREAD_PANEL_FILTER_PARAMS, useThreadPanelFilters } from "../hooks/use-thread-panel-filters";
import { SelectionReadStatus, SelectionStarredStatus } from "@/features/providers/thread-selection";
import { LabelsWidget } from "@/features/layouts/components/labels-widget";
import useAbility, { Abilities } from "@/hooks/use-ability";

type ThreadPanelTitleProps = {
    selectedThreadIds: Set<string>;
    isAllSelected: boolean;
    isSomeSelected: boolean;
    isSelectionMode: boolean;
    selectionReadStatus: SelectionReadStatus;
    selectionStarredStatus: SelectionStarredStatus;
    onSelectAll: () => void;
    onClearSelection: () => void;
    onEnableSelectionMode: () => void;
    onDisableSelectionMode: () => void;
}

const ThreadPanelTitle = ({ selectedThreadIds, isAllSelected, isSomeSelected, isSelectionMode, selectionReadStatus, selectionStarredStatus, onSelectAll, onClearSelection, onEnableSelectionMode, onDisableSelectionMode }: ThreadPanelTitleProps) => {
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

    const { activeFilters } = useThreadPanelFilters();

    // Whether at least one selected thread has full edit rights — gates
    // shared-state mutations (archive, spam, trash). Star and read/unread
    // are personal state on the user's ThreadAccess and remain available
    // regardless.
    const canEditSelection = useCanEditThreads(selectedThreadIds);

    const title = useMemo(() => {
        if (searchParams.has('search')) return t('folder.search', { defaultValue: 'Search' });
        if (searchParams.has('label_slug')) return (labelsQuery.data?.data || []).find((label) => label.slug === searchParams.get('label_slug'))?.name;
        // Thread panel filters stack on top of the folder filter — strip them
        // so the matching resolves to the underlying folder.
        const folderParams = new URLSearchParams(searchParams.toString());
        THREAD_PANEL_FILTER_PARAMS.forEach((param) => folderParams.delete(param));
        const activeFolder = findRootFolder((folder) => new URLSearchParams(folder.filter).toString() === folderParams.toString());
        return activeFolder?.name;
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

    const markAllTooltip = isSomeSelected ? t('Mark as read') : t('Mark all as read');
    const markAllUnreadLabel = isSomeSelected ? t('Mark as unread') : t('Mark all as unread');
    const mainReadTooltip = selectionReadStatus === SelectionReadStatus.READ ? markAllUnreadLabel : markAllTooltip;

    const spamLabel = isSpamView ? t('Remove spam report') : t('Report as spam');
    const spamIconName = isSpamView ? 'report_off' : 'report';
    const spamMutation = isSpamView ? markAsNotSpam : markAsSpam;

    const archiveLabel = isArchivedView ? t('Unarchive') : t('Archive');
    const archiveIconName = isArchivedView ? 'unarchive' : 'archive';
    const archiveMutation = isArchivedView ? markAsUnarchived : markAsArchived;

    const trashLabel = isTrashedView ? t('Undelete') : t('Delete');
    const trashIconName = isTrashedView ? 'restore_from_trash' : 'delete';
    const trashMutation = isTrashedView ? markAsUntrashed : markAsTrashed;

    const starLabel = t('Star');
    const unstarLabel = t('Unstar');

    const canArchive = canEditSelection && !isSpamView && !isTrashedView && !isDraftsView;
    const canReportSpam = canEditSelection && !isTrashedView && !isSentView && !isDraftsView;
    const canTrash = canEditSelection && !isDraftsView;
    const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, selectedMailbox);
    const canAssignLabel = canManageLabels && !isSpamView && !isTrashedView && !isDraftsView;
    const hasSelectionActions = canArchive || canReportSpam || canTrash || canAssignLabel;

    const countLabel = useMemo(() => {
        if (isSearch) {
            if (activeFilters.has_mention && activeFilters.has_unread && activeFilters.has_starred) {
                return t('{{count}} unread starred results mentioning you', { count: threads?.count, defaultValue_one: '{{count}} unread starred result mentioning you' });
            }
            if (activeFilters.has_mention && activeFilters.has_unread) {
                return t('{{count}} unread results mentioning you', { count: threads?.count, defaultValue_one: '{{count}} unread result mentioning you' });
            }
            if (activeFilters.has_mention && activeFilters.has_starred) {
                return t('{{count}} starred results mentioning you', { count: threads?.count, defaultValue_one: '{{count}} starred result mentioning you' });
            }
            if (activeFilters.has_mention) {
                return t('{{count}} results mentioning you', { count: threads?.count, defaultValue_one: '{{count}} result mentioning you' });
            }
            if (activeFilters.has_unread && activeFilters.has_starred) {
                return t('{{count}} unread starred results', { count: threads?.count, defaultValue_one: '{{count}} unread starred result' });
            }
            if (activeFilters.has_unread) {
                return t('{{count}} unread results', { count: threads?.count, defaultValue_one: '{{count}} unread result' });
            }
            if (activeFilters.has_starred) {
                return t('{{count}} starred results', { count: threads?.count, defaultValue_one: '{{count}} starred result' });
            }
            if (activeFilters.has_assigned_to_me) {
                return t('{{count}} results assigned to you', { count: threads?.count, defaultValue_one: '{{count}} result assigned to you' });
            }
            return t('{{count}} results', { count: threads?.count, defaultValue_one: '{{count}} result' });
        }
        else {
            if (activeFilters.has_mention && activeFilters.has_unread && activeFilters.has_starred) {
                return t('{{count}} unread starred messages mentioning you', { count: threads?.count, defaultValue_one: '{{count}} unread starred message mentioning you' });
            }
            if (activeFilters.has_mention && activeFilters.has_unread) {
                return t('{{count}} unread messages mentioning you', { count: threads?.count, defaultValue_one: '{{count}} unread message mentioning you' });
            }
            if (activeFilters.has_mention && activeFilters.has_starred) {
                return t('{{count}} starred messages mentioning you', { count: threads?.count, defaultValue_one: '{{count}} starred message mentioning you' });
            }
            if (activeFilters.has_mention) {
                return t('{{count}} messages mentioning you', { count: threads?.count, defaultValue_one: '{{count}} message mentioning you' });
            }
            if (activeFilters.has_unread && activeFilters.has_starred) {
                return t('{{count}} unread starred messages', { count: threads?.count, defaultValue_one: '{{count}} unread starred message' });
            }
            if (activeFilters.has_unread) {
                return t('{{count}} unread messages', { count: threads?.count, defaultValue_one: '{{count}} unread message' });
            }
            if (activeFilters.has_starred) {
                return t('{{count}} starred messages', { count: threads?.count, defaultValue_one: '{{count}} starred message' });
            }
            if (activeFilters.has_assigned_to_me) {
                return t('{{count}} messages assigned to you', { count: threads?.count, defaultValue_one: '{{count}} message assigned to you' });
            }
            return t('{{count}} messages', { count: threads?.count, defaultValue_one: '{{count}} message' });
        }
    }, [activeFilters, isSearch, threads?.count, t]);

    return (
        <header className="thread-panel__header">
            <div className="thread-panel__header--title-row">
                <h2 className="thread-panel__header--title">{title}</h2>
                <ThreadPanelFilter />
            </div>
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
                <p className="thread-panel__header--count" title={countLabel}>
                    {countLabel}
                </p>
                <div className="thread-panel__bar">
                    <Tooltip content={mainReadTooltip}>
                        <Button
                            onClick={() => {
                                markAsReadAt({
                                    threadIds: threadIdsToMark,
                                    readAt: selectionReadStatus === SelectionReadStatus.READ ? null : new Date().toISOString(),
                                    onSuccess: () => {
                                        unselectThread();
                                        onClearSelection();
                                    }
                                });
                            }}
                            icon={<Icon name={selectionReadStatus === SelectionReadStatus.READ ? 'mark_email_unread' : 'mark_email_read'} type={IconType.OUTLINED} />}
                            variant="tertiary"
                            size="nano"
                            aria-label={mainReadTooltip}
                        />
                    </Tooltip>
                    {isSelectionMode && (
                        <>
                            {hasSelectionActions && <VerticalSeparator withPadding={false} />}
                            {canArchive && (
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
                            {canReportSpam && (
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
                            {canTrash && (
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
                            )}
                            {canAssignLabel && (
                                <LabelsWidget
                                    threadIds={Array.from(selectedThreadIds)}
                                />
                            )}
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
                            ...([SelectionReadStatus.MIXED, SelectionReadStatus.UNREAD].includes(selectionReadStatus) ? [{
                                label: markAllTooltip,
                                icon: <span className="material-icons">mark_email_read</span>,
                                callback: () => {
                                    markAsReadAt({
                                        threadIds: threadIdsToMark,
                                        readAt: new Date().toISOString(),
                                        onSuccess: () => {
                                            unselectThread();
                                            onClearSelection();
                                        }
                                    });
                                },
                            }] : []),
                            ...([SelectionReadStatus.MIXED, SelectionReadStatus.READ, SelectionReadStatus.NONE].includes(selectionReadStatus) ? [{
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
                                    });
                                },
                            }] : []),
                            ...(isSelectionMode && selectedThreadIds.size > 0 && ([SelectionStarredStatus.MIXED, SelectionStarredStatus.UNSTARRED, SelectionStarredStatus.NONE].includes(selectionStarredStatus)) ? [{
                                label: starLabel,
                                icon: <Icon name="star_border" type={IconType.OUTLINED} />,
                                callback: () => {
                                    markAsStarred({
                                        threadIds: threadIdsToMark,
                                        onSuccess: () => {
                                            unselectThread();
                                            onClearSelection();
                                        }
                                    });
                                },
                            }] : []),
                            ...(isSelectionMode && selectedThreadIds.size > 0 && ([SelectionStarredStatus.MIXED, SelectionStarredStatus.STARRED].includes(selectionStarredStatus)) ? [{
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

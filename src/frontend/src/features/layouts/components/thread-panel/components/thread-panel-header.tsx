import clsx from "clsx";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useCurrentFolderName } from "@/hooks/use-current-folder-name";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useState } from "react";
import { Button, Tooltip, Checkbox } from "@gouvfr-lasuite/cunningham-react";
import useRead from "@/features/message/use-read";
import { DropdownMenu, IconType, VerticalSeparator, useResponsive } from "@gouvfr-lasuite/ui-kit";
import ViewHelper from "@/features/utils/view-helper";
import useArchive from "@/features/message/use-archive";
import useSpam from "@/features/message/use-spam";
import useTrash from "@/features/message/use-trash";
import useDeleteDrafts from "@/features/message/use-delete-drafts";
import useStarred from "@/features/message/use-starred";
import useCanEditThreads from "@/features/message/use-can-edit-threads";
import { ThreadPanelFilter } from "./thread-panel-filter";
import { useThreadPanelFilters } from "../hooks/use-thread-panel-filters";
import { SelectionReadStatus, SelectionStarredStatus } from "@/features/providers/thread-selection";
import { LabelsWidget } from "@/features/layouts/components/labels-widget";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { isNativePlatform } from "@/features/native/platform";
import { Archive, MoreVertical, Restore, Star, StarFilled, TodoList, Trash, Error as ErrorIcon } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon, IconProps } from "@/features/ui/components/icon";

type ThreadPanelTitleProps = {
    selectedThreadIds: Set<string>;
    isAllSelected: boolean;
    isSomeSelected: boolean;
    isSelectionMode: boolean;
    selectionReadStatus: SelectionReadStatus;
    selectionStarredStatus: SelectionStarredStatus;
    onSelectAll: () => void;
    onDeselectAll: () => void;
    onClearSelection: () => void;
    onEnableSelectionMode: () => void;
    onDisableSelectionMode: () => void;
    isRefreshing?: boolean;
    refreshFeedback?: string | null;
    onClearRefreshFeedback?: () => void;
}

/** How long the pull-to-refresh feedback replaces the message count. */
const REFRESH_FEEDBACK_DURATION_MS = 4000;

const ThreadPanelTitle = ({ selectedThreadIds, isAllSelected, isSomeSelected, isSelectionMode, selectionReadStatus, selectionStarredStatus, onSelectAll, onDeselectAll, onClearSelection, onEnableSelectionMode, onDisableSelectionMode, isRefreshing, refreshFeedback, onClearRefreshFeedback }: ThreadPanelTitleProps) => {
    const { t } = useTranslation();
    const { isDesktop, isMobile } = useResponsive();
    const { markAsReadAt } = useRead();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { deleteDrafts } = useDeleteDrafts();
    const { markAsSpam, markAsNotSpam } = useSpam();
    const { markAsStarred, markAsUnstarred } = useStarred();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const searchParams = useUrlSearchParams();
    const isSearch = searchParams.has('search');
    const { mailboxes, threads, selectedMailbox, unselectThread } = useMailboxContext();
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

    const title = useCurrentFolderName() ?? t('Messages');

    // Touch has no Escape key and no visible entry point out of selection
    // mode other than the overflow menu, so the mobile header swaps the
    // folder title and message count for a selection bar carrying an
    // explicit exit button.
    const showSelectionBar = (isMobile || isNativePlatform()) && isSelectionMode;

    // Touch turns the actions into a floating control: bigger targets inside
    // the box the rest of the app chrome uses. The native app gets it outside
    // selection mode too — its folder row has no filter button (that lives in
    // the bottom bar), so the actions have the room, and both views end up
    // with the same bar.
    const hasFloatingActions = showSelectionBar || isNativePlatform();

    // Nano (24px) is a mouse affordance: touch needs a target a fingertip can
    // hit. Small (32px) rather than medium (48px) because the selection row
    // also carries the select-all checkbox and the exit button — six 48px
    // actions would not fit a 375px screen.
    const actionButtonSize = hasFloatingActions ? 'small' : 'nano';

    const handleSelectAllToggle = () => {
        if (!isAllSelected) {
            onSelectAll();
        } else if (showSelectionBar) {
            // On touch the checkbox is a pure select/deselect toggle: leaving
            // selection mode is the exit button's job, and dropping out of it
            // here would strand the user with a long press as the only way
            // back in.
            onDeselectAll();
        } else {
            onClearSelection();
        }
    };

    const threadIdsToMark = useMemo(() => {
        if (selectedThreadIds.size > 0) {
            return Array.from(selectedThreadIds);
        }
        return threads?.results.map((thread) => thread.id) || [];
    }, [selectedThreadIds, threads?.results]);

    const isNative = isNativePlatform();
    const markAllTooltip = isSomeSelected ? t('Mark as read') : t('Mark all as read');
    const markAllUnreadLabel = isSomeSelected ? t('Mark as unread') : t('Mark all as unread');
    const mainReadTooltip = selectionReadStatus === SelectionReadStatus.READ ? markAllUnreadLabel : markAllTooltip;

    const spamLabel = isSpamView ? t('Remove spam report') : t('Report as spam');
    const spamIconProps: IconProps = isSpamView ? { name: 'error-off' } : { icon: ErrorIcon };
    const spamMutation = isSpamView ? markAsNotSpam : markAsSpam;

    const archiveLabel = isArchivedView ? t('Unarchive') : t('Archive');
    const archiveIconProps: IconProps = isArchivedView ? { name: 'inbox' } : { icon: Archive };
    const archiveMutation = isArchivedView ? markAsUnarchived : markAsArchived;

    const trashLabel = isTrashedView ? t('Undelete') : t('Delete');
    const trashIconProps: IconProps = { icon: isTrashedView ? Restore : Trash };
    const trashMutation = isTrashedView ? markAsUntrashed : markAsTrashed;

    const starLabel = t('Star');
    const unstarLabel = t('Unstar');

    const canStarSelection = !isSpamView && !isTrashedView;
    const canArchive = canEditSelection && !isSpamView && !isTrashedView && !isDraftsView;
    const canReportSpam = canEditSelection && !isTrashedView && !isSentView && !isDraftsView;
    const canTrash = canEditSelection && !isDraftsView;
    const canDeleteDrafts = canEditSelection && isDraftsView;
    const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, selectedMailbox);
    const canAssignLabel = canManageLabels && !isSpamView && !isTrashedView && !isDraftsView;
    const hasSelectionActions = canArchive || canReportSpam || canTrash || canDeleteDrafts || canAssignLabel;

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

    // Surface the pull-to-refresh result in place of the count for a moment,
    // then revert to the count once the feedback has been seen.
    useEffect(() => {
        if (!refreshFeedback) return;
        const timeoutId = window.setTimeout(
            () => onClearRefreshFeedback?.(),
            REFRESH_FEEDBACK_DURATION_MS,
        );
        return () => window.clearTimeout(timeoutId);
    }, [refreshFeedback, onClearRefreshFeedback]);

    const detailsLabel = isRefreshing
        ? t('Fetching mail…')
        : refreshFeedback ?? countLabel;

    // With several accessible mailboxes, prefix the count with the active
    // mailbox so the user always knows which box they are looking at. Only on
    // viewports where the left panel (and its mailbox selector) is hidden —
    // on desktop the selector already shows it.
    const mailboxName = selectedMailbox?.name?.trim() || selectedMailbox?.email;
    const showMailboxName = !isDesktop && (mailboxes?.length ?? 0) > 1 && !!mailboxName;
    const detailsTitle = showMailboxName ? `${mailboxName} · ${detailsLabel}` : detailsLabel;

    // Same control either side of the selection bar / count switch below.
    const selectAllCheckbox = (isSelectionMode || isSomeSelected) && (
        <Checkbox
            checked={isAllSelected}
            indeterminate={isSomeSelected && !isAllSelected}
            onChange={handleSelectAllToggle}
            aria-label={isAllSelected ? t('Deselect all threads') : t('Select all threads')}
            className="thread-panel__header--checkbox"
        />
    );

    const actionsBar = (
        <div className={clsx("thread-panel__bar", {
            "thread-panel__bar--floating": hasFloatingActions,
        })}>
            <Tooltip content={mainReadTooltip}>
                <Button
                    onClick={() => {
                        // Close the open thread before firing the mutation. Waiting for
                        // onSuccess would let the visibility observer re-observe the
                        // newly-unread messages and debounce a mark-as-read that silently
                        // reverts the action.
                        unselectThread();
                        onClearSelection();
                        markAsReadAt({
                            threadIds: threadIdsToMark,
                            readAt: selectionReadStatus === SelectionReadStatus.READ ? null : new Date().toISOString(),
                        });
                    }}
                    icon={<Icon name={selectionReadStatus === SelectionReadStatus.READ ? 'mail-unread' : 'mail-open'} type={IconType.OUTLINED} />}
                    variant="tertiary"
                    size={actionButtonSize}
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
                                icon={<Icon {...archiveIconProps} />}
                                variant="tertiary"
                                size={actionButtonSize}
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
                                icon={<Icon {...spamIconProps} />}
                                variant="tertiary"
                                size={actionButtonSize}
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
                                icon={<Icon {...trashIconProps} />}
                                variant="tertiary"
                                size={actionButtonSize}
                                aria-label={trashLabel}
                            />
                        </Tooltip>
                    )}
                    {canDeleteDrafts && (
                        <Tooltip content={t('Delete drafts')} className={selectedThreadIds.size === 0 ? 'hidden' : ''}>
                            <Button
                                onClick={() => {
                                    deleteDrafts({
                                        threadIds: threadIdsToMark,
                                        onSuccess: () => {
                                            unselectThread();
                                            onClearSelection();
                                        }
                                    });
                                }}
                                disabled={selectedThreadIds.size === 0}
                                icon={<Icon name="edit_off" type={IconType.OUTLINED} />}
                                variant="tertiary"
                                size={actionButtonSize}
                                aria-label={t('Delete draft')}
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
                        icon: <Icon icon={TodoList} />,
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
                        icon: <Icon name="mail-open" />,
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
                        icon: <Icon name="mail-unread" />,
                        callback: () => {
                            // Close the open thread before the mutation so the visibility
                            // observer cannot re-mark the newly-unread messages as read.
                            unselectThread();
                            onClearSelection();
                            markAsReadAt({
                                threadIds: threadIdsToMark,
                                readAt: null,
                            });
                        },
                    }] : []),
                    ...(canStarSelection && isSelectionMode && selectedThreadIds.size > 0 && ([SelectionStarredStatus.MIXED, SelectionStarredStatus.UNSTARRED, SelectionStarredStatus.NONE].includes(selectionStarredStatus)) ? [{
                        label: starLabel,
                        icon: <Star />,
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
                    ...(canStarSelection && isSelectionMode && selectedThreadIds.size > 0 && ([SelectionStarredStatus.MIXED, SelectionStarredStatus.STARRED].includes(selectionStarredStatus)) ? [{
                        label: unstarLabel,
                        icon: <StarFilled />,
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
                        icon={<Icon icon={MoreVertical} />}
                        variant="tertiary"
                        aria-label={t('More options')}
                        size={actionButtonSize}
                    />
                </Tooltip>
            </DropdownMenu>
        </div>
    );

    const headerText = (
        <>
            <div className="thread-panel__header--title-row">
                {showSelectionBar ? (
                    <h2 className="thread-panel__header--title">
                        {t('{{count}} selected threads', { count: selectedThreadIds.size })}
                    </h2>
                ) : (
                    <>
                        <h2 className="thread-panel__header--title">{title}</h2>
                        {/* On the native app the filter lives in the bottom bar. */}
                        {!isNative && <ThreadPanelFilter />}
                    </>
                )}
            </div>
            <div className={clsx("thread-panel__header--details", {
                "thread-panel__header--details--selection": showSelectionBar,
            })}>
                {showSelectionBar ? (
                    // Select-all and exit sit in a box of their own, mirroring
                    // the bulk-action bar opposite. The count and mailbox name
                    // step aside: on a narrow screen the row has to fit both
                    // boxes, and the selection size is already the title above.
                    <div className="thread-panel__selection-controls">
                        {selectAllCheckbox}
                        <Button
                            onClick={onDisableSelectionMode}
                            icon={<Icon name="close" type={IconType.OUTLINED} />}
                            variant="tertiary"
                            color="neutral"
                            size={actionButtonSize}
                            aria-label={t('Disable thread selection')}
                        />
                    </div>
                ) : (
                    <>
                        {selectAllCheckbox}
                        <p className="thread-panel__header--count" title={detailsTitle}>
                            {showMailboxName && (
                                <>
                                    <span className="thread-panel__header--mailbox">{mailboxName}</span>
                                    <span aria-hidden="true"> · </span>
                                </>
                            )}
                            {detailsLabel}
                        </p>
                    </>
                )}
                {/* Off touch the actions stay inline with the count. */}
                {!hasFloatingActions && actionsBar}
            </div>
        </>
    );

    const headerSelection = (
        <>
            <div className={clsx("thread-panel__header--details", {
                "thread-panel__header--details--selection": showSelectionBar,
            })}>
                {/* Off touch the actions stay inline with the count. */}
                {!hasFloatingActions && actionsBar}
                <div className="thread-panel__selection-controls">
                    {selectAllCheckbox}
                    <Button
                        onClick={onDisableSelectionMode}
                        icon={<Icon name="close" type={IconType.OUTLINED} />}
                        variant="tertiary"
                        color="neutral"
                        size={actionButtonSize}
                        aria-label={t('Disable thread selection')}
                    />
                </div>
            </div>
        </>
    );

    if (isNative) {
        return (
            <header className="thread-panel__header">
                {hasFloatingActions ? (
                    // The box around the actions would deepen the count row;
                    // stacking the title and count in their own column instead
                    // lets the bar sit alongside both, keeping the header short.
                    // Selection mode reflows this into a grid (see the SCSS): its
                    // title is a running count too long to share a row with the bar.
                    <div className={clsx("thread-panel__header--columns", {
                        "thread-panel__header--columns--selection": showSelectionBar,
                    })}>
                        <div className="thread-panel__header--text">
                            {isSelectionMode ? headerSelection : headerText}
                        </div>
                        {isSelectionMode ? actionsBar : null}
                    </div>
                ) : headerText}
            </header>
        );
    }

    return (
        <header className="thread-panel__header">
            {hasFloatingActions ? (
                // The box around the actions would deepen the count row;
                // stacking the title and count in their own column instead
                // lets the bar sit alongside both, keeping the header short.
                // Selection mode reflows this into a grid (see the SCSS): its
                // title is a running count too long to share a row with the bar.
                <div className={clsx("thread-panel__header--columns", {
                    "thread-panel__header--columns--selection": showSelectionBar,
                })}>
                    <div className="thread-panel__header--text">{headerText}</div>
                    {actionsBar}
                </div>
            ) : headerText}
        </header>
    )
}

export default ThreadPanelTitle;

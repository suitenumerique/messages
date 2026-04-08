import { ThreadsStatsRetrieve200, ThreadsStatsRetrieveStatsFields, useThreadsStatsRetrieve } from "@/features/api/gen"
import { getThreadsStatsQueryKey, useMailboxContext } from "@/features/providers/mailbox"
import clsx from "clsx"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useMemo, useState } from "react"
import { useLayoutContext } from "../../../main"
import { useTranslation } from "react-i18next"
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit"
import i18n from "@/features/i18n/initI18n";
import useArchive from "@/features/message/use-archive";
import useTrash from "@/features/message/use-trash";
import useSpam from "@/features/message/use-spam";
import { handle } from "@/features/utils/errors";
import ViewHelper from "@/features/utils/view-helper";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { EXPANDED_FOLDERS_KEY } from "@/features/config/constants"

// @TODO: replace with real data when folder will be ready
type Folder = {
    id: string;
    name: string;
    icon: string;
    filter?: Record<string, string>;
    showStats: boolean;
    searchable?: boolean;
    conditional?: boolean;
    children?: Folder[];
}

export const MAILBOX_FOLDERS = () => [
    {
        id: "inbox",
        name: i18n.t("Inbox"),
        icon: "inbox",
        searchable: false,
        showStats: true,
        filter: {
            has_active: "1"
        },
        children: [
            {
                id: "unread",
                name: i18n.t("Unread"),
                icon: "mark_email_unread",
                searchable: false,
                showStats: true,
                filter: {
                    has_active: "1",
                    has_unread: "1",
                },
            },
            {
                id: "starred",
                name: i18n.t("Starred"),
                icon: "star",
                searchable: false,
                showStats: true,
                filter: {
                    has_active: "1",
                    has_starred: "1",
                },
            },
            {
                id: "mentioned",
                name: i18n.t("Mentioned"),
                icon: "alternate_email",
                searchable: false,
                showStats: true,
                filter: {
                    has_active: "1",
                    has_mention: "1",
                },
            },
        ],
    },
    {
        id: "drafts",
        name: i18n.t("Drafts"),
        icon: "mode_edit",
        searchable: true,
        showStats: true,
        filter: {
            has_draft: "1",
        },
    },
    {
        id: "outbox",
        name: i18n.t("Outbox"),
        icon: "schedule_send",
        searchable: false,
        conditional: true,
        showStats: true,
        filter: {
            has_sender: "1",
            has_delivery_pending: "1"
        },
    },
    {
        id: "sent",
        name: i18n.t("Sent"),
        icon: "outbox",
        searchable: true,
        showStats: true,
        filter: {
            has_sender: "1",
            has_delivery_pending: "0"
        },
    },
    {
        id: "archives",
        name: i18n.t("Archives"),
        icon: "inventory_2",
        searchable: true,
        showStats: true,
        filter: {
            has_archived: "1",
        },
    },
    {
        id: "spam",
        name: i18n.t("Spam"),
        icon: "report",
        searchable: true,
        showStats: true,
        filter: {
            is_spam: "1",
        },
    },
    {
        id: "trash",
        name: i18n.t("Trash"),
        icon: "delete",
        searchable: true,
        showStats: false,
        filter: {
            has_trashed: "1",
        },
    },
] as const;

/**
 * Virtual "All messages" folder. Not displayed in the sidebar — it represents
 * the absence of any folder filter ("show everything"). Exposed separately so
 * that the search filters form (and any view that needs the concept) can
 * reference it without polluting MAILBOX_FOLDERS with a non-sidebar entry.
 */
export const ALL_MESSAGES_FOLDER = () => ({
    id: "all_messages" as const,
    name: i18n.t("All messages"),
    icon: "mark_as_unread",
    showStats: true,
    filter: {
        has_messages: "1",
    },
});

/**
 * Combines multiple stats fields into a comma-separated string for the API.
 * The API accepts a comma-separated list of fields (e.g., "all,has_delivery_failed").
 * This function provides type-safety for the individual field values while
 * producing the combined string format the API expects.
 */
const combineStatsFields = (
    ...fields: ThreadsStatsRetrieveStatsFields[]
): ThreadsStatsRetrieveStatsFields => {
    // The API type doesn't model comma-separated values, but the backend accepts them.
    // This cast is intentional - see backend/core/api/viewsets/thread.py:200-205
    return fields.join(',') as ThreadsStatsRetrieveStatsFields;
};

/**
 * Finds the root folder in the MAILBOX_FOLDERS tree structure whose match
 * (either directly or through one of its children) satisfies the predicate.
 * A child match still returns the root folder so consumers always get the
 * top-level entry (e.g. "Inbox" rather than "Unread").
 */
export const findRootFolder = (predicate: (folder: Folder) => boolean): Folder | undefined => {
    for (const folder of MAILBOX_FOLDERS() as readonly Folder[]) {
        if (predicate(folder)) return folder;
        if (folder.children?.some(predicate)) return folder;
    }
    return undefined;
};

export const MailboxList = () => {
    const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>(() => {
        if (typeof window === 'undefined') return { 'inbox': true };
        const savedState = localStorage.getItem(EXPANDED_FOLDERS_KEY);
        if (savedState === null) return { 'inbox': true };
        return JSON.parse(savedState) as Record<string, boolean>;
    });

    /**
    * Toggle the expanded state of a folder and save the state to localStorage.
    */
    const toggleFolder = (folderId: string) => {
        setExpandedFolders((prev) => {
            const nextState = {
                ...prev,
                [folderId]: !prev[folderId],
            };
            if (typeof window !== 'undefined') {
                localStorage.setItem(EXPANDED_FOLDERS_KEY, JSON.stringify(nextState));
            }
            return nextState;
        });
    };

    return (
        <nav className="mailbox-list">
            {(MAILBOX_FOLDERS() as readonly Folder[]).map((folder) => (
                <div key={folder.id}>
                    <FolderItem
                        folder={folder}
                        hasChildren={!!folder.children?.length}
                        isExpanded={!!expandedFolders[folder.id]}
                        onToggleExpand={() => toggleFolder(folder.id)}
                        childrenContainerId={`mailbox-children-${folder.id}`}
                    />
                    {folder.children && (
                        <div
                            id={`mailbox-children-${folder.id}`}
                            className={clsx("mailbox__children", {
                                "mailbox__children--collapsed": !expandedFolders[folder.id],
                            })}
                        >
                            {folder.children.map((child) => (
                                <FolderItem
                                    key={child.id}
                                    folder={child}
                                    isChild
                                />
                            ))}
                        </div>
                    )}
                </div>
            ))}
        </nav>
    )
}

type FolderItemProps = {
    folder: Folder;
    isChild?: boolean;
    hasChildren?: boolean;
    isExpanded?: boolean;
    onToggleExpand?: () => void;
    // Id of the children container the expand/collapse button toggles.
    // Used as the target of `aria-controls` so screen readers can follow
    // the disclosure relationship.
    childrenContainerId?: string;
}

// Folders that accept thread drops
const DROPPABLE_FOLDER_IDS = ['inbox', 'archives', 'spam', 'trash'] as const;
type DroppableFolderId = typeof DROPPABLE_FOLDER_IDS[number];

const FolderItem = ({ folder, isChild, hasChildren, isExpanded, onToggleExpand, childrenContainerId }: FolderItemProps) => {
    const { t } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const searchParams = useSearchParams()
    const [isDragOver, setIsDragOver] = useState(false);

    // Hooks for thread actions
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { markAsSpam, markAsNotSpam } = useSpam();

    const queryParams = useMemo(() => {
        const params = new URLSearchParams(Object.entries(folder.filter || {}));
        return params.toString();
    }, [folder.filter]);
    const stats_fields = useMemo(() => {
        if (folder.id === 'drafts') return ThreadsStatsRetrieveStatsFields.all;
        if (folder.id === 'outbox') return ThreadsStatsRetrieveStatsFields.all;
        if (folder.id === 'mentioned') return ThreadsStatsRetrieveStatsFields.has_unread_mention;
        return ThreadsStatsRetrieveStatsFields.all_unread;
    }, [folder.id]);
    const { data } = useThreadsStatsRetrieve({
        mailbox_id: selectedMailbox?.id,
        stats_fields: folder.id === "outbox"
            ? combineStatsFields(stats_fields, ThreadsStatsRetrieveStatsFields.has_delivery_failed)
            : stats_fields,
        ...folder.filter
    }, {
        query: {
            enabled: folder.showStats,
            queryKey: getThreadsStatsQueryKey(selectedMailbox!.id, queryParams),
        }
    });

    const folderStats = data?.data as ThreadsStatsRetrieve200;

    // View checks for determining allowed actions (same logic as thread-panel-header.tsx)
    const isTrashedView = ViewHelper.isTrashedView();
    const isSpamView = ViewHelper.isSpamView();
    const isArchivedView = ViewHelper.isArchivedView();
    const isDraftsView = ViewHelper.isDraftsView();
    const isSentView = ViewHelper.isSentView();

    // Determine if this folder can accept drops based on current view
    const isDroppable = useMemo(() => {
        if (!DROPPABLE_FOLDER_IDS.includes(folder.id as DroppableFolderId)) return false;

        switch (folder.id) {
            case 'inbox':
                // Inbox accepts drops to restore threads from archive, spam, or trash views
                return isArchivedView || isSpamView || isTrashedView;
            case 'archives':
                // Archive allowed from all views except drafts and archives itself
                return !isDraftsView && !isSpamView && !isTrashedView && !isArchivedView;
            case 'spam':
                // Spam not allowed from trash, sent, or drafts views
                return !isTrashedView && !isSentView && !isDraftsView && !isSpamView;
            case 'trash':
                // Trash not allowed from drafts view
                return !isDraftsView && !isTrashedView;
            default:
                return false;
        }
    }, [folder.id, isArchivedView, isSpamView, isTrashedView, isDraftsView, isSentView]);

    const isFolderActive = (folder: Folder): boolean => {
        if (hasChildren === true && isExpanded) {
            const hasChildrenActive = folder.children?.some((child) => isFolderActive(child)) ?? false;
            if (hasChildrenActive) return false;
        }

        const folderFilter = Object.entries(folder.filter || {});
        return folderFilter.every(([key, value]) => {
            return searchParams.get(key) === value;
        });
    };
    const isActive = isFolderActive(folder);

    const folderCount = folderStats?.[stats_fields] ?? 0;
    const hasDeliveryFailed = (folderStats?.[ThreadsStatsRetrieveStatsFields.has_delivery_failed] ?? 0) > 0;

    const handleDragOver = (e: React.DragEvent<HTMLAnchorElement>) => {
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = 'link';
        setIsDragOver(true);
    };

    const handleDragLeave = () => {
        setIsDragOver(false);
    };

    const handleDrop = (e: React.DragEvent<HTMLAnchorElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);

        const rawData = e.dataTransfer.getData('application/json');
        if (!rawData) return;

        try {
            const data = JSON.parse(rawData);
            if (data.type !== 'thread' || !data.threadIds?.length) return;

            const threadIds = data.threadIds as string[];

            // Call the appropriate action based on folder
            switch (folder.id) {
                case 'inbox':
                    // Restore threads based on current view with toast and undo
                    if (isArchivedView) {
                        markAsUnarchived({
                            threadIds,
                            onSuccess: () => {
                                addToast(
                                    <ToasterItem
                                        type="info"
                                        actions={[{ label: t('Undo'), onClick: () => markAsArchived({ threadIds }) }]}
                                    >
                                        <Icon name="unarchive" type={IconType.OUTLINED} />
                                        <span>{t('{{count}} threads have been unarchived.', { count: threadIds.length, defaultValue_one: 'The thread has been unarchived.' })}</span>
                                    </ToasterItem>
                                );
                            }
                        });
                    } else if (isSpamView) {
                        markAsNotSpam({
                            threadIds,
                            onSuccess: () => {
                                addToast(
                                    <ToasterItem
                                        type="info"
                                        actions={[{ label: t('Undo'), onClick: () => markAsSpam({ threadIds }) }]}
                                    >
                                        <Icon name="report_off" type={IconType.OUTLINED} />
                                        <span>{t('Spam report removed from {{count}} threads.', { count: threadIds.length, defaultValue_one: 'Spam report removed from the thread.' })}</span>
                                    </ToasterItem>
                                );
                            }
                        });
                    } else if (isTrashedView) {
                        markAsUntrashed({
                            threadIds,
                            onSuccess: () => {
                                addToast(
                                    <ToasterItem
                                        type="info"
                                        actions={[{ label: t('Undo'), onClick: () => markAsTrashed({ threadIds }) }]}
                                    >
                                        <Icon name="restore_from_trash" type={IconType.OUTLINED} />
                                        <span>{t('{{count}} threads have been restored.', { count: threadIds.length, defaultValue_one: 'The thread has been restored.' })}</span>
                                    </ToasterItem>
                                );
                            }
                        });
                    }
                    break;
                case 'archives':
                    markAsArchived({ threadIds });
                    break;
                case 'spam':
                    markAsSpam({ threadIds });
                    break;
                case 'trash':
                    markAsTrashed({ threadIds });
                    break;
            }
        } catch (error) {
            handle(new Error('Error parsing drag data.'), { extra: { error } });
        }
    };

    if (folder.conditional && folderCount === 0) {
        return null;
    }

    // Disclosure button label. Including the folder name gives the button
    // a self-standing accessible name (e.g. "Expand Inbox") instead of a
    // bare "Expand" which would force screen reader users to rely on the
    // preceding link context to understand what is being toggled.
    const chevronLabel = isExpanded
        ? t("Collapse {{name}}", { name: t(folder.name) })
        : t("Expand {{name}}", { name: t(folder.name) });

    const link = (
        <Link
            href={`/mailbox/${selectedMailbox?.id}?${queryParams}`}
            onClick={closeLeftPanel}
            shallow={false}
            className={clsx("mailbox__item", {
                "mailbox__item--active": isActive,
                "mailbox__item--drag-over": isDragOver,
                "mailbox__item--child": isChild,
                "mailbox__item--with-chevron": hasChildren,
            })}
            onDragOver={isDroppable ? handleDragOver : undefined}
            onDragLeave={isDroppable ? handleDragLeave : undefined}
            onDrop={isDroppable ? handleDrop : undefined}
        >
            <p className="mailbox__item-label">
                <Icon name={folder.icon} type={IconType.OUTLINED} aria-hidden="true" size={IconSize.SMALL} />
                {t(folder.name)}
            </p>
            <div className="mailbox__item__metadata">
            {
                hasDeliveryFailed ? <Tooltip content={t("Some messages have not been delivered to all recipients.")} placement="left"><Icon name="error" type={IconType.OUTLINED} aria-label={t("Delivery failed")} className="mailbox__item-warning" /></Tooltip>:
                folderCount > 0 && <span className="mailbox__item-counter">{folderCount}</span>
            }
            </div>
        </Link>
    );

    if (!hasChildren) {
        return link;
    }

    // Wrap the link and the disclosure button as siblings so the two
    // interactive controls stay accessible (interactive content cannot be
    // nested inside an <a>) and the link's accessible name stays clean.
    return (
        <div className="mailbox__item-row">
            <button
                type="button"
                className={clsx("mailbox__item-chevron", {
                    "mailbox__item-chevron--collapsed": !isExpanded,
                })}
                onClick={onToggleExpand}
                aria-expanded={isExpanded}
                aria-controls={childrenContainerId}
                aria-label={chevronLabel}
            >
                <Icon name="expand_more" type={IconType.OUTLINED} aria-hidden="true" />
            </button>
            {link}
        </div>
    )
}

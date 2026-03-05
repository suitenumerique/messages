import { ThreadsStatsRetrieve200, ThreadsStatsRetrieveStatsFields, useThreadsStatsRetrieve } from "@/features/api/gen"
import { useMailboxContext } from "@/features/providers/mailbox"
import clsx from "clsx"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useMemo, useState } from "react"
import { useLayoutContext } from "../../../main"
import { useTranslation } from "react-i18next"
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit"
import i18n from "@/features/i18n/initI18n";
import useArchive from "@/features/message/use-archive";
import useTrash from "@/features/message/use-trash";
import useSpam from "@/features/message/use-spam";
import { handle } from "@/features/utils/errors";
import ViewHelper from "@/features/utils/view-helper";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { THREAD_PANEL_FILTER_PARAMS } from "../../../thread-panel/components/thread-panel-filter"

// @TODO: replace with real data when folder will be ready
type Folder = {
    id: string;
    name: string;
    icon: string;
    filter?: Record<string, string>;
    searchable?: boolean;
    conditional?: boolean;
}

export const MAILBOX_FOLDERS = () => [
    {
        id: "inbox",
        name: i18n.t("Inbox"),
        icon: "inbox",
        searchable: false,
        filter: {
            has_active: "1"
        },
    },
    {
        id: "all_messages",
        name: i18n.t("All messages"),
        icon: "mark_as_unread",
        searchable: true,
        filter: {
            has_messages: "1"
        },
    },
    {
        id: "drafts",
        name: i18n.t("Drafts"),
        icon: "mode_edit",
        searchable: true,
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
        filter: {
            has_archived: "1",
        },
    },
    {
        id: "spam",
        name: i18n.t("Spam"),
        icon: "report",
        searchable: true,
        filter: {
            is_spam: "1",
        },
    },
    {
        id: "trash",
        name: i18n.t("Trash"),
        icon: "delete",
        searchable: true,
        filter: {
            has_trashed: "1",
        },
    },
] as const satisfies readonly Folder[];

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

export const MailboxList = () => {

    return (
        <nav className="mailbox-list">
            {MAILBOX_FOLDERS().map((folder) => (
                <FolderItem
                    key={folder.icon}
                    folder={folder}
                />
            ))}
        </nav>
    )
}

type FolderItemProps = {
    folder: Folder
}

// Folders that accept thread drops
const DROPPABLE_FOLDER_IDS = ['inbox', 'archives', 'spam', 'trash'] as const;
type DroppableFolderId = typeof DROPPABLE_FOLDER_IDS[number];

const FolderItem = ({ folder }: FolderItemProps) => {
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
            queryKey: ['threads', 'stats', selectedMailbox!.id, queryParams],
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

    const isActive = useMemo(() => {
        const folderFilter = Object.entries(folder.filter || {});
        // Exclude thread panel filter params from comparison so filters don't break folder matching
        const folderParamsSize = Array.from(searchParams.keys()).filter(
            (key) => !THREAD_PANEL_FILTER_PARAMS.includes(key as (typeof THREAD_PANEL_FILTER_PARAMS)[number])
        ).length;
        if (folderFilter.length !== folderParamsSize) return false;

        return folderFilter.every(([key, value]) => {
            return searchParams.get(key) === value;
        });
    }, [searchParams, folder.filter]);

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

    const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        closeLeftPanel();
        // Prevent navigation if already on this folder
        if (isActive) {
            e.preventDefault();
        }
    };

    if (folder.conditional && folderCount === 0) {
        return null;
    }

    return (
        <Link
            href={`/mailbox/${selectedMailbox?.id}?${queryParams}`}
            onClick={handleClick}
            shallow={false}
            className={clsx("mailbox__item", {
                "mailbox__item--active": isActive,
                "mailbox__item--drag-over": isDragOver
            })}
            onDragOver={isDroppable ? handleDragOver : undefined}
            onDragLeave={isDroppable ? handleDragLeave : undefined}
            onDrop={isDroppable ? handleDrop : undefined}
        >
            <p className="mailbox__item-label">
                <Icon name={folder.icon} type={IconType.OUTLINED} aria-hidden="true" />
                {t(folder.name)}
            </p>
            <div className="mailbox__item__metadata">
            {
                hasDeliveryFailed ? <Tooltip content={t("Some messages have not been delivered to all recipients.")} placement="left"><Icon name="error" type={IconType.OUTLINED} aria-label={t("Delivery failed")} className="mailbox__item-warning" /></Tooltip>:
                folderCount > 0 && <span className="mailbox__item-counter">{folderCount}</span>
            }
            </div>
        </Link>
    )
}

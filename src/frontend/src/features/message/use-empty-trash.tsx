import { useEffect } from "react";
import { useModals } from "@gouvfr-lasuite/ui-components";
import { Trash } from "@gouvfr-lasuite/ui-components/icons";
import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
    getMailboxesEntitlementsRetrieveQueryKey,
    getMailboxesStorageRetrieveQueryKey,
    Mailbox,
    threadsStatsRetrieveResponse,
    useMailboxesEmptyTrashCreate,
} from "@/features/api/gen";
import { MailboxEmptyTrashRequestScopeEnum, StatusEnum } from "@/features/api/gen/models";
import { errorToString } from "@/features/api/api-error";
import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";
import ViewHelper, { ViewName } from "@/features/utils/view-helper";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useTaskStatus } from "@/hooks/use-task-status";
import { getFolderStatsQueryKey, useFolderStats } from "@/hooks/use-threads-stats";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useMailboxContext } from "../providers/mailbox";
import { Icon } from "../ui/components/icon";

type EmptyTrashOptions = {
    mailboxId: string;
    scope: MailboxEmptyTrashRequestScopeEnum;
    /** Restrict the deletion to these conversations. Omit to empty the folder. */
    threadIds?: string[];
    onSuccess?: () => void;
};

/** A deletion the backend handed to a background task (large folders). */
type EmptyTrashTask = {
    taskId: string;
    /** A selection rather than the whole folder: only changes the final toast. */
    targeted: boolean;
    /** Epoch milliseconds, to give up on a task that never finishes. */
    startedAt: number;
};

// Celery states after which the task will not change any more.
const FINISHED_TASK_STATES = new Set<string>([StatusEnum.SUCCESS, StatusEnum.FAILURE, StatusEnum.REVOKED]);

// A task Celery lost (worker killed, result expired) stays PENDING forever.
// Past this age it is no longer followed and the folder is refreshed instead,
// so the banner never stays stuck on "being emptied".
export const EMPTY_TRASH_TASK_MAX_AGE_MS = 60 * 60 * 1000;


const getEmptyTrashTaskQueryKey = (mailboxId: string | undefined, scope: MailboxEmptyTrashRequestScopeEnum) =>
    ["empty-trash-task", mailboxId, scope] as const;

const TRASHBIN_FOLDER_IDS = {
    trashed: "trash",
    spam: "spam",
} as const satisfies Record<MailboxEmptyTrashRequestScopeEnum, ViewName>;

const getTrashbinFolder = (scope: MailboxEmptyTrashRequestScopeEnum) =>
    MAILBOX_FOLDERS().find(({ id }) => id === TRASHBIN_FOLDER_IDS[scope])!;

const getEmptyTrashTaskStorageKey = (mailboxId: string, scope: MailboxEmptyTrashRequestScopeEnum) =>
    `messages.empty-trash-task.${mailboxId}.${scope}`;

// Storage can be unavailable (private browsing, blocked site data): a running
// task is then only tracked until the page is reloaded.
const readEmptyTrashTask = (mailboxId: string, scope: MailboxEmptyTrashRequestScopeEnum): EmptyTrashTask | null => {
    try {
        const raw = localStorage.getItem(getEmptyTrashTaskStorageKey(mailboxId, scope));
        if (!raw) return null;
        const task: unknown = JSON.parse(raw);
        if (
            typeof task === "object" && task !== null
            && "taskId" in task && typeof task.taskId === "string"
            && "targeted" in task && typeof task.targeted === "boolean"
            && "startedAt" in task && typeof task.startedAt === "number"
        ) {
            // Expired while no page was following it: the folder is fetched
            // fresh anyway, so there is nothing to refresh.
            if (Date.now() - task.startedAt > EMPTY_TRASH_TASK_MAX_AGE_MS) {
                localStorage.removeItem(getEmptyTrashTaskStorageKey(mailboxId, scope));
                return null;
            }
            return { taskId: task.taskId, targeted: task.targeted, startedAt: task.startedAt };
        }
        return null;
    } catch {
        return null;
    }
};

/**
 * Record (or clear, with `null`) the background task emptying a folder. It
 * goes to localStorage so it survives a reload, and to the query cache so the
 * watcher and every mounted `useEmptyTrashTask` see it at once.
 */
const setEmptyTrashTask = (
    queryClient: QueryClient,
    mailboxId: string,
    scope: MailboxEmptyTrashRequestScopeEnum,
    task: EmptyTrashTask | null,
) => {
    try {
        const storageKey = getEmptyTrashTaskStorageKey(mailboxId, scope);
        if (task) {
            localStorage.setItem(storageKey, JSON.stringify(task));
        } else {
            localStorage.removeItem(storageKey);
        }
    } catch {
        // See readEmptyTrashTask: tracked in memory only.
    }
    queryClient.setQueryData(getEmptyTrashTaskQueryKey(mailboxId, scope), task);
};

type TrashbinRefreshOptions = {
    mailboxId: string;
    /** The deleted conversations when known, else every listed one is unpinned. */
    threadIds?: string[];
};

/** Refresh everything a permanent deletion changes. */
const useTrashbinRefresh = () => {
    const queryClient = useQueryClient();
    const { threads, unpinThreads, invalidateMailbox, invalidateThreadsStats } = useMailboxContext();

    return ({ mailboxId, threadIds }: TrashbinRefreshOptions) => {
        // Drop the pins (e.g. set when a thread is read) before refetching,
        // otherwise the deleted threads would be merged back into the list.
        unpinThreads(threadIds ?? threads?.results.map((thread) => thread.id) ?? []);
        invalidateMailbox();
        invalidateThreadsStats();
        // The backend drops its cached usage on deletion, but the gauge and
        // the storage tab would otherwise keep showing it until their staleTime.
        queryClient.invalidateQueries({ queryKey: getMailboxesStorageRetrieveQueryKey(mailboxId) });
        queryClient.invalidateQueries({ queryKey: getMailboxesEntitlementsRetrieveQueryKey(mailboxId) });
    };
};

/**
 * Refresh everything a permanent deletion changes and confirm it, whether it
 * ran within the request or in a background task.
 */
const useOnTrashEmptied = () => {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const refresh = useTrashbinRefresh();

    return ({ mailboxId, scope, targeted, threadIds }: TrashbinRefreshOptions & {
        scope: MailboxEmptyTrashRequestScopeEnum;
        targeted: boolean;
    }) => {
        // The folder is known to be empty now: its counts (banner button,
        // sidebar badge) drop at once instead of waiting for the stats to be
        // fetched again.
        if (!targeted) {
            queryClient.setQueryData<threadsStatsRetrieveResponse>(
                getFolderStatsQueryKey(mailboxId, getTrashbinFolder(scope)),
                (response) => response?.status === 200
                    ? { ...response, data: Object.fromEntries(Object.keys(response.data).map((field) => [field, 0])) }
                    : response,
            );
        }
        refresh({ mailboxId, threadIds });

        // A background task does not report which threads it deleted.
        const deletedCount = threadIds?.length ?? 0;
        const toastMessage = targeted
            ? deletedCount > 0
                ? t("{{count}} threads have been permanently deleted.", {
                    count: deletedCount,
                    defaultValue_one: "The thread has been permanently deleted.",
                })
                : t("The selected messages have been permanently deleted.")
            : scope === "spam"
              ? t("Spam emptied.")
              : t("Trash emptied.");
        addToast(
            <ToasterItem type="info">
                <Icon icon={Trash} />
                <p>{toastMessage}</p>
            </ToasterItem>
        );
    };
};

/**
 * Permanently delete from one folder of the trashbin (trashed OR spam). Unlike
 * `useTrash` (a reversible soft-delete flag), this hard-deletes and cannot be
 * undone — so it always goes through a scary confirmation dialog first and its
 * toast never offers "Undo".
 *
 * Deletes the whole folder by default; pass `threadIds` to remove only a
 * selection. Both go through the same backend action and the same
 * `empty_trash` permission — deleting one trashed conversation is exactly as
 * irreversible as emptying the folder, so it is gated identically.
 *
 * Large deletions run in a background task, followed by
 * `useEmptyTrashTasksWatcher` and shown by the folder banner.
 */
const useEmptyTrash = () => {
    const { t } = useTranslation();
    const modals = useModals();
    const queryClient = useQueryClient();
    const onTrashEmptied = useOnTrashEmptied();
    const { mutate, isPending } = useMailboxesEmptyTrashCreate();

    const emptyTrashbin = async ({
        mailboxId,
        scope,
        threadIds,
        onSuccess,
    }: EmptyTrashOptions) => {
        if (isPending) return;
        const isSpam = scope === "spam";
        // A selection was passed, so this is a targeted delete rather than a
        // whole-folder wipe: a single thread (e.g. the open one) is named as such.
        const targetedCount = threadIds?.length ?? 0;
        const isTargeted = targetedCount > 0;
        const title = isTargeted
            ? t("Delete permanently")
            : isSpam
              ? t("Empty spam")
              : t("Empty trash");
        const message = isTargeted
            ? t("You are about to permanently delete {{count}} threads. This cannot be undone.", {
                count: targetedCount,
                defaultValue_one: "You are about to permanently delete this thread. This cannot be undone.",
            })
            : isSpam
              ? t("You are about to permanently delete every thread in spam. This cannot be undone.")
              : t("You are about to permanently delete every thread in the trash. This cannot be undone.");

        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{title}</span>,
            children: message,
        });

        if (decision !== "delete") return;

        mutate(
            {
                id: mailboxId,
                data: {
                    scope,
                    ...(threadIds?.length ? { thread_ids: threadIds } : {}),
                },
            },
            {
                onSuccess: (response) => {
                    if (response.status === 202) {
                        setEmptyTrashTask(queryClient, mailboxId, scope, {
                            taskId: response.data.task_id,
                            targeted: isTargeted,
                            startedAt: Date.now(),
                        });
                        addToast(
                            <ToasterItem type="info">
                                <p>{t("The deletion is in progress. It may take a few minutes.")}</p>
                            </ToasterItem>
                        );
                    } else {
                        onTrashEmptied({ mailboxId, scope, targeted: isTargeted, threadIds: isTargeted ? threadIds : undefined });
                    }
                    onSuccess?.();
                },
                onError: (error) => {
                    addToast(
                        <ToasterItem type="error">
                            <p>{errorToString(error)}</p>
                        </ToasterItem>
                    );
                },
            }
        );
    };

    return { emptyTrashbin, isPending };
};

const useStoredEmptyTrashTask = (mailboxId: string | undefined, scope: MailboxEmptyTrashRequestScopeEnum) => {
    const readStoredTask = () => (mailboxId ? readEmptyTrashTask(mailboxId, scope) : null);
    const { data: task = null } = useQuery({
        queryKey: getEmptyTrashTaskQueryKey(mailboxId, scope),
        queryFn: readStoredTask,
        // Read synchronously, so a task resumed after a reload shows at once.
        initialData: readStoredTask,
        // Only ever changed through setEmptyTrashTask.
        staleTime: Infinity,
    });
    return task;
};

/** Whether a background task is emptying this folder. */
export const useEmptyTrashTask = (mailboxId: string | undefined, scope: MailboxEmptyTrashRequestScopeEnum) => {
    return { isRunning: useStoredEmptyTrashTask(mailboxId, scope) !== null };
};

/**
 * Number of conversations in a folder of the trashbin, i.e. what emptying it
 * deletes, whatever search or filter narrows the thread list. Being a stats
 * query, it is refreshed by every action that moves threads in or out of the
 * folder (`invalidateThreadsStats`). Shares the sidebar request of the folder.
 */
export const useTrashbinFolderCount = (mailbox: Mailbox | null, scope: MailboxEmptyTrashRequestScopeEnum) => {
    const canEmptyTrash = useAbility(Abilities.CAN_EMPTY_TRASH, mailbox);
    // Only the empty button needs it.
    const { stats } = useFolderStats(mailbox?.id, getTrashbinFolder(scope), { enabled: canEmptyTrash });
    return stats?.all;
};

/**
 * Keep the Trash and Spam counts of the selected mailbox fetched, so the
 * folder banner knows whether to offer emptying as soon as the folder opens.
 * Mounted once, in the main layout.
 */
export const useTrashbinFolderCounts = () => {
    const { selectedMailbox } = useMailboxContext();
    useTrashbinFolderCount(selectedMailbox, "trashed");
    useTrashbinFolderCount(selectedMailbox, "spam");
};

/**
 * Follow the background task emptying a folder, if any — including one started
 * before a reload. Once it finishes, it refreshes and confirms like a deletion
 * that ran within the request, or reports the failure. Past
 * EMPTY_TRASH_TASK_MAX_AGE_MS it gives up and only refreshes.
 */
const useEmptyTrashTaskWatcher = (mailboxId: string | undefined, scope: MailboxEmptyTrashRequestScopeEnum) => {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const refresh = useTrashbinRefresh();
    const onTrashEmptied = useOnTrashEmptied();
    const task = useStoredEmptyTrashTask(mailboxId, scope);
    const taskStatus = useTaskStatus(task?.taskId ?? null, { refetchInterval: 2000 });
    const taskState = taskStatus?.state;
    // Pins only matter for the listed threads, which are the deleted ones only
    // when the emptied folder is the one on screen.
    const getDeletedThreadIds = () => {
        const isFolderView = scope === "spam" ? ViewHelper.isSpamView() : ViewHelper.isTrashedView();
        return isFolderView ? undefined : [];
    };

    useEffect(() => {
        if (!mailboxId || !task || !taskState || !FINISHED_TASK_STATES.has(taskState)) return;

        setEmptyTrashTask(queryClient, mailboxId, scope, null);
        if (taskState === StatusEnum.SUCCESS) {
            onTrashEmptied({ mailboxId, scope, targeted: task.targeted, threadIds: getDeletedThreadIds() });
        } else {
            addToast(
                <ToasterItem type="error">
                    <p>{taskStatus?.error ?? t("The deletion could not be completed.")}</p>
                </ToasterItem>
            );
        }
    }, [taskState, task, mailboxId, scope]);

    useEffect(() => {
        if (!mailboxId || !task) return;
        const timer = window.setTimeout(() => {
            setEmptyTrashTask(queryClient, mailboxId, scope, null);
            refresh({ mailboxId, threadIds: getDeletedThreadIds() });
        }, task.startedAt + EMPTY_TRASH_TASK_MAX_AGE_MS - Date.now());
        return () => window.clearTimeout(timer);
    }, [task, mailboxId, scope]);
};

/**
 * Follow the background tasks emptying the Trash and Spam folders of the
 * selected mailbox, whatever folder is on screen. Mounted once, in the main
 * layout.
 */
export const useEmptyTrashTasksWatcher = () => {
    const { selectedMailbox } = useMailboxContext();
    useEmptyTrashTaskWatcher(selectedMailbox?.id, "trashed");
    useEmptyTrashTaskWatcher(selectedMailbox?.id, "spam");
};

export default useEmptyTrash;

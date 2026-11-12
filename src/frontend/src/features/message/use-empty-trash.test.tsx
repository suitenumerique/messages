import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MAILBOX_ID = "mailbox-1";
const STORAGE_KEY = `messages.empty-trash-task.${MAILBOX_ID}.trashed`;

const useTaskStatus = vi.fn();
const addToast = vi.fn();
const unpinThreads = vi.fn();
const unselectThread = vi.fn();
const clearSelection = vi.fn();
const invalidateMailbox = vi.fn();
const invalidateThreadsStats = vi.fn();
let taskState: string | undefined;
let isTrashedView = false;
let currentView = { isSpamView: false, isTrashedView: true };
let searchStr = "";
let canEmptyTrash = true;
let folderCount: number | undefined = 3;

const deleteConfirmationModal = vi.fn();
const mutate = vi.fn();

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@gouvfr-lasuite/ui-components", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@gouvfr-lasuite/ui-components")>()),
    useModals: () => ({ deleteConfirmationModal }),
}));

vi.mock("@/features/api/gen", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@/features/api/gen")>()),
    useMailboxesEmptyTrashCreate: () => ({ mutate, isPending: false }),
}));

vi.mock("@/features/layouts/components/mailbox-panel/components/mailbox-list", () => ({
    MAILBOX_FOLDERS: () => [
        { id: "spam", filter: { is_spam: "1" }, stats: { count: "all_unread", extra: ["all"] } },
        { id: "trash", filter: { has_trashed: "1" }, stats: { count: "all" } },
    ],
}));

vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => ({
        selectedMailbox: { id: MAILBOX_ID },
        threads: { results: [{ id: "thread-1" }] },
        unpinThreads,
        unselectThread,
        invalidateMailbox,
        invalidateThreadsStats,
    }),
}));

vi.mock("@/features/providers/thread-selection", () => ({
    useThreadSelection: () => ({ clearSelection }),
}));

vi.mock("@/hooks/use-current-view", () => ({
    useCurrentView: () => currentView,
}));

vi.mock("@/hooks/use-url-search-params", () => ({
    useUrlSearchParams: () => new URLSearchParams(searchStr),
}));

vi.mock("@/hooks/use-ability", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@/hooks/use-ability")>()),
    default: () => canEmptyTrash,
}));

vi.mock("@/hooks/use-threads-stats", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@/hooks/use-threads-stats")>()),
    useFolderStats: () => ({ stats: { all: folderCount } }),
}));

vi.mock("@/features/utils/view-helper", () => ({
    default: {
        isTrashedView: () => isTrashedView,
        isSpamView: () => false,
    },
}));

vi.mock("@/hooks/use-task-status", () => ({
    useTaskStatus: (taskId: string | null) => {
        useTaskStatus(taskId);
        return taskId ? { state: taskState, error: taskState === "FAILURE" ? "boom" : null } : null;
    },
}));

vi.mock("../ui/components/toaster", () => ({
    addToast: (...args: unknown[]) => addToast(...args),
    ToasterItem: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import useEmptyTrash, { EMPTY_TRASH_TASK_MAX_AGE_MS, useEmptyCurrentFolder, useEmptyTrashTask, useEmptyTrashTasksWatcher } from "./use-empty-trash";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const Probe = () => {
    useEmptyTrashTasksWatcher();
    const { isRunning } = useEmptyTrashTask(MAILBOX_ID, "trashed");
    return <span data-running={String(isRunning)} />;
};

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

const render = async () => {
    await act(async () => {
        root.render(
            <QueryClientProvider client={queryClient}>
                <Probe />
            </QueryClientProvider>,
        );
    });
    // React Query notifies its observers on a timeout, which act does not await.
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
    });
};

const isRunning = () => container.querySelector("span")?.dataset.running === "true";

const storeTask = (startedAt = Date.now()) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ taskId: "task-1", targeted: false, startedAt }));
};

beforeEach(() => {
    taskState = undefined;
    isTrashedView = false;
    currentView = { isSpamView: false, isTrashedView: true };
    searchStr = "";
    canEmptyTrash = true;
    folderCount = 3;
    localStorage.clear();
    vi.clearAllMocks();
    queryClient = new QueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
});

describe("useEmptyTrashTasksWatcher", () => {
    it("is idle without a stored task", async () => {
        await render();

        expect(isRunning()).toBe(false);
        expect(useTaskStatus).not.toHaveBeenCalledWith("task-1");
    });

    it("resumes a task stored before a reload", async () => {
        storeTask();

        await render();

        expect(isRunning()).toBe(true);
        expect(useTaskStatus).toHaveBeenCalledWith("task-1");
    });

    it("refreshes and confirms once the task succeeds", async () => {
        storeTask();
        taskState = "SUCCESS";

        await render();

        expect(isRunning()).toBe(false);
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
        // Another folder is on screen: its listed threads were not deleted.
        expect(unpinThreads).toHaveBeenCalledWith([]);
        expect(invalidateMailbox).toHaveBeenCalled();
        expect(invalidateThreadsStats).toHaveBeenCalled();
        expect(addToast).toHaveBeenCalledTimes(1);
    });

    it("marks the emptied folder as empty without waiting for the stats", async () => {
        const statsKey = ["threads", "stats", MAILBOX_ID, "has_trashed=1&stats_fields=all"];
        queryClient.setQueryData(statsKey, { status: 200, data: { all: 12 }, headers: new Headers() });
        storeTask();
        taskState = "SUCCESS";

        await render();

        expect(queryClient.getQueryData(statsKey)).toMatchObject({ data: { all: 0 } });
    });

    it("unpins the listed threads when the emptied folder is on screen", async () => {
        storeTask();
        taskState = "SUCCESS";
        isTrashedView = true;

        await render();

        expect(unpinThreads).toHaveBeenCalledWith(["thread-1"]);
    });

    it("refreshes and reports a failed task, part of the folder may be gone", async () => {
        const statsKey = ["threads", "stats", MAILBOX_ID, "has_trashed=1&stats_fields=all"];
        queryClient.setQueryData(statsKey, { status: 200, data: { all: 12 }, headers: new Headers() });
        storeTask();
        taskState = "FAILURE";

        await render();

        expect(isRunning()).toBe(false);
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
        expect(invalidateMailbox).toHaveBeenCalled();
        expect(invalidateThreadsStats).toHaveBeenCalled();
        // Not known to be empty: the count comes from the refetch.
        expect(queryClient.getQueryData(statsKey)).toMatchObject({ data: { all: 12 } });
        expect(addToast).toHaveBeenCalledTimes(1);
    });

    it("ignores an unreadable stored value", async () => {
        localStorage.setItem(STORAGE_KEY, "not json");

        await render();

        expect(isRunning()).toBe(false);
        expect(useTaskStatus).not.toHaveBeenCalledWith("task-1");
    });

    it("drops a task that expired while no page followed it", async () => {
        storeTask(Date.now() - EMPTY_TRASH_TASK_MAX_AGE_MS - 1);

        await render();

        expect(isRunning()).toBe(false);
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
        expect(useTaskStatus).not.toHaveBeenCalledWith("task-1");
    });

    it("gives up on a task that never finishes and refreshes the folder", async () => {
        storeTask(Date.now() - EMPTY_TRASH_TASK_MAX_AGE_MS + 20);
        taskState = "PENDING";

        await render();
        expect(isRunning()).toBe(true);

        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 50));
        });

        expect(isRunning()).toBe(false);
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
        expect(invalidateMailbox).toHaveBeenCalled();
        expect(addToast).not.toHaveBeenCalled();
    });
});

describe("useEmptyTrash", () => {
    type EmptyTrash = ReturnType<typeof useEmptyTrash>["emptyTrashbin"];
    type EmptyTrashOptions = Parameters<EmptyTrash>[0];

    const ONE_THREAD = "one conversation";
    const SEVERAL_THREADS = "several conversations";
    const WHOLE_FOLDER = "whole folder";
    const targets = {
        [ONE_THREAD]: { threadIds: ["thread-1"] },
        [SEVERAL_THREADS]: { threadIds: ["thread-1", "thread-2"] },
        [WHOLE_FOLDER]: {},
    };

    const mount = async () => {
        const mounted: { emptyTrashbin?: EmptyTrash } = {};
        const Caller = ({ onRender }: { onRender: (emptyTrashbin: EmptyTrash) => void }) => {
            onRender(useEmptyTrash().emptyTrashbin);
            return null;
        };
        await act(async () => {
            root.render(
                <QueryClientProvider client={queryClient}>
                    <Caller onRender={(emptyTrashbin) => { mounted.emptyTrashbin = emptyTrashbin; }} />
                </QueryClientProvider>,
            );
        });
        return mounted.emptyTrashbin!;
    };

    /** Run a confirmed deletion, whose request answers `deleted_count`. */
    const run = async (options: Omit<EmptyTrashOptions, "mailboxId">, deletedCount: number) => {
        const emptyTrashbin = await mount();
        await act(async () => {
            await emptyTrashbin({ mailboxId: MAILBOX_ID, ...options });
        });
        const [, { onSuccess }] = mutate.mock.calls[0];
        await act(async () => {
            onSuccess({ status: 200, data: { success: true, deleted_count: deletedCount } });
        });
    };

    const modalMessage = () => deleteConfirmationModal.mock.calls[0][0].children;
    const toastMessage = () => {
        const toast = addToast.mock.calls[0][0];
        const container = document.createElement("div");
        const toastRoot = createRoot(container);
        act(() => toastRoot.render(toast));
        const text = container.querySelector("p")?.textContent;
        act(() => toastRoot.unmount());
        return text;
    };

    beforeEach(() => {
        deleteConfirmationModal.mockResolvedValue("delete");
    });

    it.each([
        ["spam", ONE_THREAD,
            "You are about to permanently delete the spam in {{count}} threads. This cannot be undone.",
            "The spam in {{count}} threads has been deleted."],
        ["spam", SEVERAL_THREADS,
            "You are about to permanently delete the spam in {{count}} threads. This cannot be undone.",
            "The spam in {{count}} threads has been deleted."],
        ["spam", WHOLE_FOLDER,
            "You are about to permanently delete all spam. This cannot be undone.",
            "Spam emptied."],
        ["trashed", ONE_THREAD,
            "You are about to permanently delete the trashed messages of {{count}} threads. This cannot be undone.",
            "The trashed messages of {{count}} threads have been deleted."],
        ["trashed", SEVERAL_THREADS,
            "You are about to permanently delete the trashed messages of {{count}} threads. This cannot be undone.",
            "The trashed messages of {{count}} threads have been deleted."],
        ["trashed", WHOLE_FOLDER,
            "You are about to permanently delete everything in the trash. This cannot be undone.",
            "Trash emptied."],
    ] as const)("names what goes for %s, %s", async (scope, target, expectedModal, expectedToast) => {
        await run({ scope, ...targets[target] }, 3);

        expect(modalMessage()).toBe(expectedModal);
        expect(toastMessage()).toBe(expectedToast);
    });

    it.each([
        ["spam", "No spam could be deleted."],
        ["trashed", "No trashed message could be deleted."],
    ] as const)("does not announce a %s deletion that removed nothing", async (scope, expectedToast) => {
        await run({ scope, threadIds: ["thread-1"] }, 0);

        expect(addToast).toHaveBeenCalledTimes(1);
        expect(toastMessage()).toBe(expectedToast);
        expect(invalidateMailbox).toHaveBeenCalled();
    });

    it("does nothing when the confirmation is declined", async () => {
        deleteConfirmationModal.mockResolvedValue("cancel");
        const emptyTrashbin = await mount();

        await act(async () => {
            await emptyTrashbin({ mailboxId: MAILBOX_ID, scope: "spam" });
        });

        expect(mutate).not.toHaveBeenCalled();
    });
});

describe("useEmptyCurrentFolder", () => {
    type EmptyCurrentFolder = ReturnType<typeof useEmptyCurrentFolder>;

    const mount = async () => {
        const mounted: { current?: EmptyCurrentFolder } = {};
        const Caller = ({ onRender }: { onRender: (folder: EmptyCurrentFolder) => void }) => {
            onRender(useEmptyCurrentFolder());
            return null;
        };
        await act(async () => {
            root.render(
                <QueryClientProvider client={queryClient}>
                    <Caller onRender={(folder) => { mounted.current = folder; }} />
                </QueryClientProvider>,
            );
        });
        return mounted.current!;
    };

    it.each([
        ["trash", { isSpamView: false, isTrashedView: true }, "Empty trash"],
        ["spam", { isSpamView: true, isTrashedView: false }, "Empty spam"],
    ])("offers to empty the %s folder", async (_, view, expectedLabel) => {
        currentView = view;

        const { canEmptyFolder, isTrashbinView, emptyFolderLabel } = await mount();

        expect(canEmptyFolder).toBe(true);
        expect(isTrashbinView).toBe(true);
        expect(emptyFolderLabel).toBe(expectedLabel);
    });

    it("does not offer it outside the trashbin folders", async () => {
        currentView = { isSpamView: false, isTrashedView: false };

        const { canEmptyFolder, isTrashbinView } = await mount();

        expect(canEmptyFolder).toBe(false);
        expect(isTrashbinView).toBe(false);
    });

    it("does not offer it during a search, which lists matches rather than the folder", async () => {
        searchStr = "search=invoice&has_trashed=1";

        const { canEmptyFolder, isTrashbinView } = await mount();

        expect(canEmptyFolder).toBe(false);
        expect(isTrashbinView).toBe(false);
    });

    it.each([0, undefined])("does not offer it when the folder holds %s threads", async (count) => {
        folderCount = count;

        expect((await mount()).canEmptyFolder).toBe(false);
    });

    it("does not offer it without the permission", async () => {
        canEmptyTrash = false;

        expect((await mount()).canEmptyFolder).toBe(false);
    });

    it("does not offer it while the folder is already being emptied", async () => {
        storeTask();

        const { canEmptyFolder, isEmptying } = await mount();

        expect(canEmptyFolder).toBe(false);
        expect(isEmptying).toBe(true);
    });

    it("empties the whole folder and only drops the open thread and selection once done", async () => {
        deleteConfirmationModal.mockResolvedValue("delete");
        const { emptyFolder } = await mount();

        await act(async () => {
            emptyFolder();
        });

        const [variables, { onSuccess }] = mutate.mock.calls[0];
        expect(variables).toEqual({ id: MAILBOX_ID, data: { scope: "trashed" } });
        expect(unselectThread).not.toHaveBeenCalled();
        expect(clearSelection).not.toHaveBeenCalled();

        await act(async () => {
            onSuccess({ status: 200, data: { success: true, deleted_count: 3 } });
        });

        expect(unselectThread).toHaveBeenCalled();
        expect(clearSelection).toHaveBeenCalled();
    });
});

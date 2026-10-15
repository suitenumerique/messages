import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MAILBOX_ID = "mailbox-1";
const STORAGE_KEY = `messages.empty-trash-task.${MAILBOX_ID}.trashed`;

const useTaskStatus = vi.fn();
const addToast = vi.fn();
const unpinThreads = vi.fn();
const invalidateMailbox = vi.fn();
const invalidateThreadsStats = vi.fn();
let taskState: string | undefined;
let isTrashedView = false;

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
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
        invalidateMailbox,
        invalidateThreadsStats,
    }),
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

import { EMPTY_TRASH_TASK_MAX_AGE_MS, useEmptyTrashTask, useEmptyTrashTasksWatcher } from "./use-empty-trash";

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

    it("reports a failed task without refreshing", async () => {
        storeTask();
        taskState = "FAILURE";

        await render();

        expect(isRunning()).toBe(false);
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
        expect(invalidateMailbox).not.toHaveBeenCalled();
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

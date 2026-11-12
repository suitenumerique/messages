import { act, ReactElement, useEffect } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChangeFlagRequestRequest } from "@/features/api/gen/models";
import useFlag from "./use-flag";

type FlagMutationOptions = {
    mutation: {
        onSuccess: (response: { data: unknown }, variables: { data: ChangeFlagRequestRequest }) => void;
    };
};
type ToastOptions = { toastId: string; onClose: () => void };

const mutate = vi.fn<(variables: { data: ChangeFlagRequestRequest }) => Promise<{ data: unknown }>>(
    () => Promise.resolve({ data: { updated_threads: 1 } }),
);
const activeToastIds = new Set<string>();
const addToast = vi.fn((_content: ReactElement, options: ToastOptions) => {
    activeToastIds.add(options.toastId);
});
const updateToast = vi.fn();
const dismissToast = vi.fn();

// Like React Query, the mutation-level `onSuccess` runs before `mutateAsync`
// settles, whether the hook is still mounted or not.
vi.mock("@/features/api/gen", () => ({
    useFlagCreate: (options: FlagMutationOptions) => ({
        mutateAsync: async (variables: { data: ChangeFlagRequestRequest }) => {
            const response = await mutate(variables);
            options.mutation.onSuccess(response, variables);
            return response;
        },
        status: "idle",
    }),
}));

vi.mock("react-toastify", () => ({
    toast: {
        isActive: (toastId: string) => activeToastIds.has(toastId),
        update: (...args: unknown[]) => updateToast(...args),
        dismiss: (...args: unknown[]) => dismissToast(...args),
    },
}));

vi.mock("../ui/components/toaster", () => ({
    addToast: (content: ReactElement, options: ToastOptions) => addToast(content, options),
    ToasterItem: ({ children, actions }: { children: React.ReactNode; actions: { label: string; onClick: () => void }[] }) => (
        <div>
            {children}
            {actions.map((action) => <button key={action.label} onClick={action.onClick}>{action.label}</button>)}
        </div>
    ),
}));

// The real hook suspends until the translation resources are loaded.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

const flagRef: { current?: ReturnType<typeof useFlag> } = {};

const HookHost = ({ onSuccess }: { onSuccess?: (data: ChangeFlagRequestRequest) => void }) => {
    const flag = useFlag("starred", {
        toastMessages: {
            thread: (updatedCount) => `${updatedCount} threads starred`,
            message: (updatedCount) => `${updatedCount} messages starred`,
        },
        onSuccess,
    });
    useEffect(() => {
        flagRef.current = flag;
    });
    return null;
};

afterEach(() => {
    // Closing the toasts releases the batches kept between tests.
    addToast.mock.calls.forEach(([, options]) => options.onClose());
    activeToastIds.clear();
    flagRef.current = undefined;
    mutate.mockClear();
    addToast.mockClear();
    updateToast.mockReset();
    dismissToast.mockClear();
});

describe("useFlag toast", () => {
    let container: HTMLDivElement;
    let root: Root;
    const onSuccess = vi.fn();

    beforeEach(() => {
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        act(() => root.render(<HookHost onSuccess={onSuccess} />));
        onSuccess.mockClear();
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    const closeToast = (call: number) => {
        const [, options] = addToast.mock.calls[call];
        options.onClose();
        activeToastIds.delete(options.toastId);
    };

    // Leaves only the undo request in the `mutate` history.
    const clickUndo = () => {
        mutate.mockClear();
        act(() => container.querySelector("button")!.click());
    };

    const flag = async (options: Parameters<ReturnType<typeof useFlag>["mark"]>[0], updatedCount: number) => {
        mutate.mockResolvedValueOnce({ data: { updated_threads: updatedCount } });
        await act(async () => flagRef.current!.mark({ mailboxId: "mailbox", ...options }));
    };

    const flagThreads = (threadIds: string[], updatedCount = threadIds.length) => flag({ threadIds }, updatedCount);

    const flagMessages = (messageIds: string[], messageThreadId: string, updatedCount: number) =>
        flag({ messageIds, messageThreadId }, updatedCount);

    const renderToast = (content: ReactElement) => {
        act(() => root.render(content));
    };

    it("merges successive flags into the visible toast", async () => {
        await flagThreads(["t1"]);
        await flagThreads(["t2"]);

        expect(addToast).toHaveBeenCalledTimes(1);
        expect(updateToast).toHaveBeenCalledTimes(1);
        const [toastId, { render }] = updateToast.mock.calls[0];
        expect(toastId).toBe(addToast.mock.calls[0][1].toastId);

        renderToast(render);
        expect(container.textContent).toContain("2 threads starred");
    });

    it("undoes every merged thread at once", async () => {
        await flagThreads(["t1"]);
        await flagThreads(["t2", "t3"]);

        renderToast(updateToast.mock.calls[0][1].render);
        clickUndo();

        expect(mutate).toHaveBeenCalledTimes(1);
        expect(mutate.mock.calls[0][0].data).toMatchObject({
            flag: "starred",
            value: false,
            thread_ids: ["t1", "t2", "t3"],
            mailbox_id: "mailbox",
        });
    });

    it("does not count twice a thread flagged twice in a row", async () => {
        await flagThreads(["t1"]);
        await flagThreads(["t1"]);

        renderToast(updateToast.mock.calls[0][1].render);
        expect(container.textContent).toContain("1 threads starred");
    });

    it("starts a new batch once the toast is closed", async () => {
        await flagThreads(["t1"]);
        closeToast(0);

        await flagThreads(["t2"]);

        expect(updateToast).not.toHaveBeenCalled();
        expect(addToast).toHaveBeenCalledTimes(2);
        renderToast(addToast.mock.calls[1][0]);
        clickUndo();
        expect(mutate.mock.calls[0][0].data.thread_ids).toEqual(["t2"]);
    });

    it("opens the new toast under another id than the one still leaving", async () => {
        await flagThreads(["t1"]);
        closeToast(0);

        await flagThreads(["t2"]);

        expect(addToast.mock.calls[1][1].toastId).not.toBe(addToast.mock.calls[0][1].toastId);
    });

    it("does not let the toast update wait before applying", async () => {
        await flagThreads(["t1"]);
        await flagThreads(["t2"]);

        expect(updateToast.mock.calls[0][1]).toMatchObject({ delay: 0 });
    });

    it("undoes the threads merged since the toast last rendered", async () => {
        await flagThreads(["t1"]);
        renderToast(addToast.mock.calls[0][0]);
        await flagThreads(["t2"]);

        clickUndo();

        expect(mutate.mock.calls[0][0].data.thread_ids).toEqual(["t1", "t2"]);
    });

    it("closes the toast on undo and leaves later flags out of it", async () => {
        await flagThreads(["t1"]);
        renderToast(addToast.mock.calls[0][0]);

        clickUndo();
        await flagThreads(["t2"]);

        expect(dismissToast).toHaveBeenCalledWith(addToast.mock.calls[0][1].toastId);
        expect(updateToast).not.toHaveBeenCalled();
        expect(addToast).toHaveBeenCalledTimes(2);
        expect(addToast.mock.calls[1][1].toastId).not.toBe(addToast.mock.calls[0][1].toastId);
    });

    it("runs the undo side effects even once the toast is gone", async () => {
        await flagThreads(["t1"]);
        renderToast(addToast.mock.calls[0][0]);
        let respond: (response: { data: unknown }) => void = () => {};
        mutate.mockReturnValueOnce(new Promise((resolve) => { respond = resolve; }));

        clickUndo();
        act(() => root.render(null));
        await act(async () => respond({ data: { updated_threads: 1 } }));

        expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ value: false, thread_ids: ["t1"] }), 1);
    });

    it("keeps reporting a failure when a thread is flagged twice", async () => {
        await flagThreads(["t1", "t2"], 1);
        await flagThreads(["t3"]);
        await flagThreads(["t1"]);

        renderToast(updateToast.mock.calls[1][1].render);
        expect(container.textContent).toContain("2 threads starred");
        expect(container.textContent).toContain("You may not have sufficient permissions for all selected threads.");
    });

    it("reports messages none of which could be flagged", async () => {
        await flagMessages(["m1", "m2"], "thread-a", 0);

        renderToast(addToast.mock.calls[0][0]);
        expect(container.textContent).toContain("0 messages starred");
        expect(container.querySelector("button")).toBeNull();
    });

    it("keeps the messages of another thread out of the toast", async () => {
        await flagMessages(["m1"], "thread-a", 1);
        await flagMessages(["m2"], "thread-b", 0);

        expect(updateToast).not.toHaveBeenCalled();
        expect(addToast).toHaveBeenCalledTimes(2);
        renderToast(addToast.mock.calls[1][0]);
        expect(container.textContent).toContain("0 messages starred");

        renderToast(addToast.mock.calls[0][0]);
        expect(container.textContent).toContain("1 messages starred");
        clickUndo();
        expect(mutate.mock.calls[0][0].data.message_ids).toEqual(["m1"]);
    });

    it("merges the messages of the same thread", async () => {
        await flagMessages(["m1"], "thread-a", 1);
        await flagMessages(["m2"], "thread-a", 1);

        expect(addToast).toHaveBeenCalledTimes(1);
        renderToast(updateToast.mock.calls[0][1].render);
        expect(container.textContent).toContain("2 messages starred");
    });
});

describe("useFlag mark", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        act(() => root.render(<HookHost />));
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("calls the onSuccess of every call flagged in a row", async () => {
        const onFirstSuccess = vi.fn();
        const onSecondSuccess = vi.fn();

        await act(async () => {
            flagRef.current!.mark({ threadIds: ["t1"], mailboxId: "mailbox", onSuccess: onFirstSuccess });
            flagRef.current!.mark({ threadIds: ["t2"], mailboxId: "mailbox", onSuccess: onSecondSuccess });
        });

        expect(onFirstSuccess).toHaveBeenCalledWith(expect.objectContaining({ thread_ids: ["t1"] }), 1);
        expect(onSecondSuccess).toHaveBeenCalledWith(expect.objectContaining({ thread_ids: ["t2"] }), 1);
    });

    it("calls the onSuccess of a caller unmounted before the response", async () => {
        const onSuccess = vi.fn();
        let respond: (response: { data: unknown }) => void = () => {};
        mutate.mockReturnValueOnce(new Promise((resolve) => { respond = resolve; }));

        flagRef.current!.mark({ threadIds: ["t1"], mailboxId: "mailbox", onSuccess });
        act(() => root.render(null));
        await act(async () => respond({ data: { updated_threads: 1 } }));

        expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ thread_ids: ["t1"] }), 1);
    });

    it("does not call onSuccess when the request fails", async () => {
        const onSuccess = vi.fn();
        mutate.mockReturnValueOnce(Promise.reject(new Error("Forbidden")));

        await act(async () => {
            flagRef.current!.mark({ threadIds: ["t1"], mailboxId: "mailbox", onSuccess });
        });

        expect(onSuccess).not.toHaveBeenCalled();
    });
});

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Thread } from "@/features/api/gen/models";
import type { ThreadRemoveAction } from "./use-thread-remove-actions";

const MAILBOX_ID = "mailbox-1";
const THREAD_ID = "thread-1";

const markAsTrashed = vi.fn();
const markAsUntrashed = vi.fn();
const deleteDrafts = vi.fn();
const emptyTrashbin = vi.fn();
const unselectThread = vi.fn();
let abilities: { canEditThread: boolean; canEmptyTrash: boolean };
let isDraftsView = false;

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => ({ selectedMailbox: { id: MAILBOX_ID }, unselectThread }),
}));

vi.mock("@/hooks/use-current-view", () => ({
    useCurrentView: () => ({ isDraftsView }),
}));

vi.mock("@/hooks/use-ability", () => ({
    Abilities: { CAN_EDIT_THREAD: "canEditThread", CAN_EMPTY_TRASH: "canEmptyTrash" },
    default: (ability: "canEditThread" | "canEmptyTrash") => abilities[ability],
}));

vi.mock("./use-trash", () => ({
    default: () => ({ markAsTrashed, markAsUntrashed }),
}));

vi.mock("./use-delete-drafts", () => ({
    default: () => ({ deleteDrafts }),
}));

vi.mock("./use-empty-trash", () => ({
    default: () => ({ emptyTrashbin, isPending: false }),
}));

import useThreadRemoveActions from "./use-thread-remove-actions";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

type ProbeProps = {
    thread: Thread;
    isTrashContext: boolean;
    onRender: (actions: ThreadRemoveAction[]) => void;
};

const Probe = ({ thread, isTrashContext, onRender }: ProbeProps) => {
    onRender(useThreadRemoveActions(thread, isTrashContext));
    return null;
};

const renderActions = (isTrashContext: boolean, { isSpam = false } = {}) => {
    const thread = { id: THREAD_ID, is_spam: isSpam } as Thread;
    let actions: ThreadRemoveAction[] = [];
    act(() => {
        root.render(
            <Probe
                thread={thread}
                isTrashContext={isTrashContext}
                onRender={(result) => { actions = result; }}
            />,
        );
    });
    return actions;
};

const keys = (list: ThreadRemoveAction[]) => list.map(({ key }) => key);
const select = (list: ThreadRemoveAction[], key: ThreadRemoveAction["key"]) =>
    list.find((action) => action.key === key)!.onSelect();

describe("useThreadRemoveActions", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        abilities = { canEditThread: true, canEmptyTrash: true };
        isDraftsView = false;
        container = document.createElement("div");
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
    });

    it("offers nothing without edit rights on the thread", () => {
        abilities.canEditThread = false;
        expect(renderActions(true)).toEqual([]);
    });

    it("offers to move a live thread to the trash", () => {
        const result = renderActions(false);
        expect(keys(result)).toEqual(["move-to-trash"]);
        select(result, "move-to-trash");
        expect(markAsTrashed).toHaveBeenCalledWith({ threadIds: [THREAD_ID], onSuccess: unselectThread });
    });

    it("only deletes the draft from the drafts view", () => {
        isDraftsView = true;
        const result = renderActions(false);
        expect(keys(result)).toEqual(["delete-draft"]);
        select(result, "delete-draft");
        expect(deleteDrafts).toHaveBeenCalledWith({ threadIds: [THREAD_ID], onSuccess: unselectThread });
    });

    it("restores or deletes the trashed messages in the trash context, never trashes", () => {
        // Also covers a partially trashed thread opened from the trash view:
        // trashing it would bin its live messages too.
        const result = renderActions(true);
        expect(keys(result)).toEqual(["restore", "delete-permanently"]);

        select(result, "restore");
        expect(markAsUntrashed).toHaveBeenCalledWith({ threadIds: [THREAD_ID], onSuccess: unselectThread });

        select(result, "delete-permanently");
        expect(emptyTrashbin).toHaveBeenCalledWith({
            mailboxId: MAILBOX_ID,
            scope: "trashed",
            threadIds: [THREAD_ID],
            onSuccess: unselectThread,
        });
        expect(markAsTrashed).not.toHaveBeenCalled();
    });

    it("permanently deletes a spam thread from the spam folder", () => {
        const result = renderActions(false, { isSpam: true });
        expect(keys(result)).toEqual(["delete-permanently"]);
        select(result, "delete-permanently");
        expect(emptyTrashbin).toHaveBeenCalledWith(expect.objectContaining({ scope: "spam" }));
    });

    it("hides permanent deletion from members who cannot empty the trash", () => {
        abilities.canEmptyTrash = false;
        expect(keys(renderActions(true))).toEqual(["restore"]);
        expect(renderActions(false, { isSpam: true })).toEqual([]);
    });
});

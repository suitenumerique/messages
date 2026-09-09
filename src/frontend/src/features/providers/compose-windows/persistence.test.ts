import { describe, expect, it } from "vitest";
import {
    clearPersistedWindows,
    deserializeWindows,
    loadPersistedWindows,
    serializeWindows,
} from "./persistence";
import { COMPOSE_WINDOWS_STORAGE_KEY } from "@/features/config";
import { ComposeWindowDescriptor } from "./types";

const makeWindow = (overrides: Partial<ComposeWindowDescriptor> = {}): ComposeWindowDescriptor => ({
    windowId: "window-1",
    mailboxId: "mailbox-1",
    mode: "new",
    presentation: "docked",
    isMinimized: false,
    openedOnExistingDraft: false,
    focusTick: 0,
    ...overrides,
});

describe("serializeWindows", () => {
    it("only persists materialized windows", () => {
        const raw = serializeWindows([
            makeWindow(),
            makeWindow({ windowId: "window-2", draftId: "draft-2" }),
        ]);
        const parsed = JSON.parse(raw);
        expect(parsed).toHaveLength(1);
        expect(parsed[0].draftId).toBe("draft-2");
    });

    it("keeps the reply context fields", () => {
        const raw = serializeWindows([
            makeWindow({
                draftId: "draft-1",
                mode: "reply",
                isMinimized: true,
                threadId: "thread-1",
                parentMessageId: "parent-1",
            }),
        ]);
        expect(JSON.parse(raw)[0]).toEqual({
            draftId: "draft-1",
            mailboxId: "mailbox-1",
            mode: "reply",
            presentation: "docked",
            isMinimized: true,
            threadId: "thread-1",
            parentMessageId: "parent-1",
        });
    });
});

describe("deserializeWindows", () => {
    it("round-trips a serialized list", () => {
        const raw = serializeWindows([
            makeWindow({ draftId: "draft-1", mode: "reply", presentation: "floating", threadId: "thread-1" }),
        ]);
        const restored = deserializeWindows(raw);
        expect(restored).toHaveLength(1);
        expect(restored[0]).toMatchObject({
            draftId: "draft-1",
            mailboxId: "mailbox-1",
            mode: "reply",
            presentation: "floating",
            isMinimized: false,
            threadId: "thread-1",
            openedOnExistingDraft: true,
            focusTick: 0,
        });
        expect(restored[0].windowId).toBeTruthy();
    });

    it("re-applies the single-expanded invariant", () => {
        const raw = serializeWindows([
            makeWindow({ draftId: "draft-1" }),
            makeWindow({ windowId: "window-2", draftId: "draft-2" }),
        ]);
        const restored = deserializeWindows(raw);
        expect(restored.filter((w) => !w.isMinimized)).toHaveLength(1);
        expect(restored.at(-1)?.isMinimized).toBe(false);
    });

    it("returns an empty list on null, corrupted or non-array payloads", () => {
        expect(deserializeWindows(null)).toEqual([]);
        expect(deserializeWindows("{not json")).toEqual([]);
        expect(deserializeWindows('{"draftId":"x"}')).toEqual([]);
    });

    it("drops entries with missing or invalid fields", () => {
        const raw = JSON.stringify([
            { draftId: "draft-1", mailboxId: "mailbox-1", mode: "new", presentation: "docked", isMinimized: false },
            { draftId: "draft-2", mailboxId: "mailbox-1", mode: "invalid-mode", presentation: "docked", isMinimized: false },
            { draftId: "draft-3", mailboxId: "mailbox-1", mode: "new", presentation: "open", isMinimized: false },
            { draftId: "draft-4", mailboxId: "mailbox-1", mode: "new", presentation: "docked" },
            { mailboxId: "mailbox-1", mode: "new", presentation: "docked", isMinimized: false },
            null,
            "junk",
        ]);
        const restored = deserializeWindows(raw);
        expect(restored).toHaveLength(1);
        expect(restored[0].draftId).toBe("draft-1");
    });
});

describe("clearPersistedWindows", () => {
    it("drops the persisted payload so nothing is restored at next boot", () => {
        localStorage.setItem(
            COMPOSE_WINDOWS_STORAGE_KEY,
            serializeWindows([makeWindow({ draftId: "draft-1" })]),
        );

        clearPersistedWindows();

        expect(localStorage.getItem(COMPOSE_WINDOWS_STORAGE_KEY)).toBeNull();
        expect(loadPersistedWindows()).toEqual([]);
    });

    it("is a no-op when nothing is persisted", () => {
        localStorage.clear();
        expect(() => clearPersistedWindows()).not.toThrow();
        expect(loadPersistedWindows()).toEqual([]);
    });
});

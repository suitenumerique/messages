import { describe, expect, it } from "vitest";
import { deserializeWindows, migrateLegacyPayload, serializeWindows } from "./persistence";
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

describe("migrateLegacyPayload", () => {
    it("maps the v1 state field onto presentation + isMinimized", () => {
        const legacy = JSON.stringify([
            { draftId: "draft-1", mailboxId: "mailbox-1", mode: "new", state: "open" },
            { draftId: "draft-2", mailboxId: "mailbox-1", mode: "reply", state: "minimized", threadId: "thread-1" },
            { draftId: "draft-3", mailboxId: "mailbox-1", mode: "new", state: "expanded" },
        ]);
        const restored = deserializeWindows(migrateLegacyPayload(legacy));
        expect(restored).toHaveLength(3);
        expect(restored.map((w) => [w.presentation, w.isMinimized])).toEqual([
            ["docked", true],
            ["docked", true],
            ["floating", false],
        ]);
        expect(restored[1].threadId).toBe("thread-1");
    });

    it("returns null on corrupted payloads", () => {
        expect(migrateLegacyPayload(null)).toBeNull();
        expect(migrateLegacyPayload("{not json")).toBeNull();
        expect(migrateLegacyPayload('{"draftId":"x"}')).toBeNull();
    });
});

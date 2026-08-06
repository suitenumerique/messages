import { describe, expect, it } from "vitest";
import { deserializeWindows, serializeWindows } from "./persistence";
import { ComposeWindowDescriptor } from "./types";

const makeWindow = (overrides: Partial<ComposeWindowDescriptor> = {}): ComposeWindowDescriptor => ({
    windowId: "window-1",
    mailboxId: "mailbox-1",
    mode: "new",
    state: "open",
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

    it("downgrades expanded to open", () => {
        const raw = serializeWindows([
            makeWindow({ draftId: "draft-1", state: "expanded" }),
        ]);
        expect(JSON.parse(raw)[0].state).toBe("open");
    });

    it("keeps the reply context fields", () => {
        const raw = serializeWindows([
            makeWindow({
                draftId: "draft-1",
                mode: "reply",
                state: "minimized",
                threadId: "thread-1",
                parentMessageId: "parent-1",
            }),
        ]);
        expect(JSON.parse(raw)[0]).toEqual({
            draftId: "draft-1",
            mailboxId: "mailbox-1",
            mode: "reply",
            state: "minimized",
            threadId: "thread-1",
            parentMessageId: "parent-1",
        });
    });
});

describe("deserializeWindows", () => {
    it("round-trips a serialized list", () => {
        const raw = serializeWindows([
            makeWindow({ draftId: "draft-1", mode: "reply", threadId: "thread-1" }),
        ]);
        const restored = deserializeWindows(raw);
        expect(restored).toHaveLength(1);
        expect(restored[0]).toMatchObject({
            draftId: "draft-1",
            mailboxId: "mailbox-1",
            mode: "reply",
            state: "open",
            threadId: "thread-1",
            openedOnExistingDraft: true,
            focusTick: 0,
        });
        expect(restored[0].windowId).toBeTruthy();
    });

    it("returns an empty list on null, corrupted or non-array payloads", () => {
        expect(deserializeWindows(null)).toEqual([]);
        expect(deserializeWindows("{not json")).toEqual([]);
        expect(deserializeWindows('{"draftId":"x"}')).toEqual([]);
    });

    it("drops entries with missing or invalid fields", () => {
        const raw = JSON.stringify([
            { draftId: "draft-1", mailboxId: "mailbox-1", mode: "new", state: "open" },
            { draftId: "draft-2", mailboxId: "mailbox-1", mode: "invalid-mode", state: "open" },
            { draftId: "draft-3", mailboxId: "mailbox-1", mode: "new", state: "expanded" },
            { mailboxId: "mailbox-1", mode: "new", state: "open" },
            null,
            "junk",
        ]);
        const restored = deserializeWindows(raw);
        expect(restored).toHaveLength(1);
        expect(restored[0].draftId).toBe("draft-1");
    });
});

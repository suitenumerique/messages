import { describe, expect, it } from "vitest";
import {
    enforceSingleExpanded,
    focusWindowReducer,
    MAX_WINDOWS,
    minimizeWindowReducer,
    openWindowReducer,
    restoreWindowReducer,
    setPresentationReducer,
} from "./core";
import { ComposeWindowDescriptor } from "./types";

let counter = 0;
const makeWindow = (overrides: Partial<ComposeWindowDescriptor> = {}): ComposeWindowDescriptor => ({
    windowId: `window-${++counter}`,
    mailboxId: "mailbox-1",
    mode: "new",
    presentation: "docked",
    isMinimized: false,
    openedOnExistingDraft: false,
    focusTick: 0,
    ...overrides,
});

const expandedIds = (windows: ComposeWindowDescriptor[]) =>
    windows.filter((w) => !w.isMinimized).map((w) => w.windowId);

describe("enforceSingleExpanded", () => {
    it("leaves a compliant list untouched", () => {
        const windows = [makeWindow({ isMinimized: true }), makeWindow()];
        expect(enforceSingleExpanded(windows)).toBe(windows);
    });

    it("keeps only the last expanded window expanded", () => {
        const windows = [makeWindow(), makeWindow({ isMinimized: true }), makeWindow()];
        const result = enforceSingleExpanded(windows);
        expect(expandedIds(result)).toEqual([windows[2].windowId]);
    });
});

describe("openWindowReducer", () => {
    it("appends the new window and minimizes the current one", () => {
        const current = makeWindow({ title: "Current subject" });
        const { windows, windowId, outcome } = openWindowReducer([current], makeWindow({ windowId: "new-window" }));
        expect(outcome).toBe("opened");
        expect(windowId).toBe("new-window");
        expect(windows.map((w) => w.isMinimized)).toEqual([true, false]);
    });

    it("focuses the existing window instead of duplicating a draft", () => {
        const existing = makeWindow({ draftId: "draft-1", isMinimized: true, focusTick: 0 });
        const other = makeWindow();
        const { windows, windowId, outcome } = openWindowReducer(
            [existing, other],
            makeWindow({ draftId: "draft-1" }),
        );
        expect(outcome).toBe("focused-existing");
        expect(windowId).toBe(existing.windowId);
        expect(windows).toHaveLength(2);
        expect(expandedIds(windows)).toEqual([existing.windowId]);
        expect(windows[0].focusTick).toBe(1);
        expect(windows[0].windowId).toBe(existing.windowId);
    });

    it("focuses an existing untitled new-message window instead of stacking blanks", () => {
        const blank = makeWindow({ mode: "new", isMinimized: true });
        const { windows, windowId, outcome } = openWindowReducer([blank], makeWindow({ mode: "new" }));
        expect(outcome).toBe("focused-existing");
        expect(windowId).toBe(blank.windowId);
        expect(windows).toHaveLength(1);
        expect(windows[0].isMinimized).toBe(false);
    });

    it("focuses an existing unmaterialized reply on the same parent message", () => {
        const reply = makeWindow({ mode: "reply", parentMessageId: "parent-1", title: "Re: subject", isMinimized: true });
        const { windows, windowId, outcome } = openWindowReducer(
            [reply],
            makeWindow({ mode: "reply", parentMessageId: "parent-1" }),
        );
        expect(outcome).toBe("focused-existing");
        expect(windowId).toBe(reply.windowId);
        expect(windows).toHaveLength(1);
    });

    it("opens a fresh window for a reply on a different parent message", () => {
        const reply = makeWindow({ mode: "reply", parentMessageId: "parent-1" });
        const { outcome, windows } = openWindowReducer(
            [reply],
            makeWindow({ mode: "reply", parentMessageId: "parent-2" }),
        );
        expect(outcome).toBe("opened");
        expect(windows).toHaveLength(2);
    });

    it("still opens a fresh window when the existing new-message ones have a subject or a draft", () => {
        const titled = makeWindow({ mode: "new", title: "Some subject" });
        const materialized = makeWindow({ mode: "new", draftId: "draft-9" });
        const { windows, outcome } = openWindowReducer([titled, materialized], makeWindow({ mode: "new" }));
        expect(outcome).toBe("opened");
        expect(windows).toHaveLength(3);
    });

    it("refuses to open beyond the hard cap", () => {
        const prev = Array.from({ length: MAX_WINDOWS }, (_, i) => makeWindow({ isMinimized: true, title: `Window ${i}` }));
        const { windows, windowId, outcome } = openWindowReducer(prev, makeWindow());
        expect(outcome).toBe("cap-reached");
        expect(windowId).toBeNull();
        expect(windows).toHaveLength(MAX_WINDOWS);
    });
});

describe("minimizeWindowReducer", () => {
    it("collapses the target window", () => {
        const window = makeWindow();
        const result = minimizeWindowReducer([window], window.windowId);
        expect(result[0].isMinimized).toBe(true);
    });

    it("is a no-op on an already minimized window", () => {
        const windows = [makeWindow({ isMinimized: true })];
        expect(minimizeWindowReducer(windows, windows[0].windowId)).toBe(windows);
    });
});

describe("restoreWindowReducer", () => {
    it("expands the target in place and minimizes the others", () => {
        const [a, b, c] = [makeWindow({ isMinimized: true }), makeWindow(), makeWindow({ isMinimized: true })];
        const result = restoreWindowReducer([a, b, c], a.windowId);
        expect(result.map((w) => w.windowId)).toEqual([a.windowId, b.windowId, c.windowId]);
        expect(expandedIds(result)).toEqual([a.windowId]);
    });

    it("moves the target to the end when asked (out of the overflow)", () => {
        const [a, b] = [makeWindow({ isMinimized: true }), makeWindow()];
        const result = restoreWindowReducer([a, b], a.windowId, { moveToEnd: true });
        expect(result.map((w) => w.windowId)).toEqual([b.windowId, a.windowId]);
        expect(expandedIds(result)).toEqual([a.windowId]);
    });

    it("keeps the remembered presentation on restore", () => {
        const floating = makeWindow({ presentation: "floating", isMinimized: true });
        const result = restoreWindowReducer([floating], floating.windowId);
        expect(result[0].presentation).toBe("floating");
        expect(result[0].isMinimized).toBe(false);
    });

    it("ignores an unknown windowId", () => {
        const windows = [makeWindow()];
        expect(restoreWindowReducer(windows, "missing")).toBe(windows);
    });
});

describe("setPresentationReducer", () => {
    it("changes the presentation of the expanded window", () => {
        const window = makeWindow();
        const result = setPresentationReducer([window], window.windowId, "floating");
        expect(result[0].presentation).toBe("floating");
        expect(result[0].isMinimized).toBe(false);
    });

    it("expands a minimized window and minimizes the current one", () => {
        const [current, minimized] = [makeWindow(), makeWindow({ isMinimized: true })];
        const result = setPresentationReducer([current, minimized], minimized.windowId, "floating");
        expect(expandedIds(result)).toEqual([minimized.windowId]);
        expect(result.at(-1)?.presentation).toBe("floating");
    });
});

describe("focusWindowReducer", () => {
    it("restores a minimized window and bumps its focus tick", () => {
        const window = makeWindow({ isMinimized: true, focusTick: 3 });
        const result = focusWindowReducer([window], window.windowId);
        expect(result[0].isMinimized).toBe(false);
        expect(result[0].focusTick).toBe(4);
    });
});

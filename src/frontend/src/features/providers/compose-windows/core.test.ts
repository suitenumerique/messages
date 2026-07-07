import { describe, expect, it } from "vitest";
import {
    enforceVisibleCap,
    focusWindowReducer,
    MAX_VISIBLE_WINDOWS,
    MAX_WINDOWS,
    openWindowReducer,
    setWindowStateReducer,
} from "./core";
import { ComposeWindowDescriptor, ComposeWindowDisplayState } from "./types";

let counter = 0;
const makeWindow = (overrides: Partial<ComposeWindowDescriptor> = {}): ComposeWindowDescriptor => ({
    windowId: `window-${++counter}`,
    mailboxId: "mailbox-1",
    mode: "new",
    state: "open",
    openedOnExistingDraft: false,
    focusTick: 0,
    ...overrides,
});

describe("enforceVisibleCap", () => {
    it("leaves the list untouched under the cap", () => {
        const windows = [makeWindow(), makeWindow({ state: "minimized" })];
        expect(enforceVisibleCap(windows)).toBe(windows);
    });

    it("minimizes the oldest visible windows beyond the cap", () => {
        const windows = [
            makeWindow(),
            makeWindow({ state: "minimized" }),
            makeWindow(),
            makeWindow({ state: "expanded" }),
            makeWindow(),
        ];
        const result = enforceVisibleCap(windows, 3);
        expect(result.map((w) => w.state)).toEqual(["minimized", "minimized", "open", "expanded", "open"]);
    });
});

describe("openWindowReducer", () => {
    it("appends the new window", () => {
        const { windows, windowId, outcome } = openWindowReducer([], makeWindow({ windowId: "new-window" }));
        expect(outcome).toBe("opened");
        expect(windowId).toBe("new-window");
        expect(windows).toHaveLength(1);
    });

    it("focuses the existing window instead of duplicating a draft", () => {
        const existing = makeWindow({ draftId: "draft-1", state: "minimized", focusTick: 0 });
        const { windows, windowId, outcome } = openWindowReducer(
            [existing],
            makeWindow({ draftId: "draft-1" }),
        );
        expect(outcome).toBe("focused-existing");
        expect(windowId).toBe(existing.windowId);
        expect(windows).toHaveLength(1);
        expect(windows[0].state).toBe("open");
        expect(windows[0].focusTick).toBe(1);
    });

    it("minimizes the oldest visible window when the visible cap is reached", () => {
        const prev = Array.from({ length: MAX_VISIBLE_WINDOWS }, () => makeWindow());
        const { windows } = openWindowReducer(prev, makeWindow({ windowId: "latest" }));
        expect(windows).toHaveLength(MAX_VISIBLE_WINDOWS + 1);
        expect(windows[0].state).toBe("minimized");
        expect(windows.at(-1)?.windowId).toBe("latest");
        expect(windows.at(-1)?.state).toBe("open");
    });

    it("refuses to open beyond the hard cap", () => {
        const prev = Array.from({ length: MAX_WINDOWS }, () => makeWindow({ state: "minimized" }));
        const { windows, windowId, outcome } = openWindowReducer(prev, makeWindow());
        expect(outcome).toBe("cap-reached");
        expect(windowId).toBeNull();
        expect(windows).toHaveLength(MAX_WINDOWS);
    });
});

describe("setWindowStateReducer", () => {
    it("updates the target window state", () => {
        const window = makeWindow();
        const result = setWindowStateReducer([window], window.windowId, "minimized");
        expect(result[0].state).toBe("minimized");
    });

    it("collapses any other expanded window when expanding", () => {
        const first = makeWindow({ state: "expanded" });
        const second = makeWindow();
        const result = setWindowStateReducer([first, second], second.windowId, "expanded");
        expect(result.map((w) => w.state)).toEqual(["open", "expanded"]);
    });

    it("re-applies the visible cap when restoring a window", () => {
        const windows = [
            ...Array.from({ length: MAX_VISIBLE_WINDOWS }, () => makeWindow()),
            makeWindow({ windowId: "restored", state: "minimized" }),
        ];
        const result = setWindowStateReducer(windows, "restored", "open" as ComposeWindowDisplayState);
        expect(result.filter((w) => w.state !== "minimized")).toHaveLength(MAX_VISIBLE_WINDOWS);
        expect(result.at(-1)?.state).toBe("open");
        expect(result[0].state).toBe("minimized");
    });
});

describe("focusWindowReducer", () => {
    it("restores a minimized window and bumps its focus tick", () => {
        const window = makeWindow({ state: "minimized", focusTick: 3 });
        const result = focusWindowReducer([window], window.windowId);
        expect(result[0].state).toBe("open");
        expect(result[0].focusTick).toBe(4);
    });
});

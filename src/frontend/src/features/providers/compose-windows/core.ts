import { ComposeWindowDescriptor, ComposeWindowDisplayState } from "./types";

/**
 * How many windows can be visible (open or expanded) at once. Opening more
 * minimizes the oldest visible one, like Gmail does when space runs out.
 */
export const MAX_VISIBLE_WINDOWS = 3;
/** Hard cap on simultaneously tracked windows (visible + minimized). */
export const MAX_WINDOWS = 8;

const isVisible = (window: ComposeWindowDescriptor) => window.state !== "minimized";

/** Minimizes the oldest visible windows until at most `max` remain visible. */
export const enforceVisibleCap = (windows: ComposeWindowDescriptor[], max = MAX_VISIBLE_WINDOWS): ComposeWindowDescriptor[] => {
    let excess = windows.filter(isVisible).length - max;
    if (excess <= 0) return windows;
    return windows.map((window) => {
        if (excess > 0 && isVisible(window)) {
            excess -= 1;
            return { ...window, state: "minimized" as const };
        }
        return window;
    });
};

/** Restores the window if minimized and bumps its focus counter. */
export const focusWindowReducer = (windows: ComposeWindowDescriptor[], windowId: string): ComposeWindowDescriptor[] =>
    windows.map((window) =>
        window.windowId === windowId
            ? {
                ...window,
                state: window.state === "minimized" ? "open" : window.state,
                focusTick: window.focusTick + 1,
            }
            : window
    );

export type OpenWindowResult = {
    windows: ComposeWindowDescriptor[];
    windowId: string | null;
    outcome: "opened" | "focused-existing" | "cap-reached";
};

/**
 * Adds a fully-built descriptor to the list. Deduplicates on draftId (the
 * existing window is focused instead) and refuses beyond the hard cap.
 */
export const openWindowReducer = (windows: ComposeWindowDescriptor[], descriptor: ComposeWindowDescriptor): OpenWindowResult => {
    if (descriptor.draftId) {
        const existing = windows.find((window) => window.draftId === descriptor.draftId);
        if (existing) {
            return {
                windows: focusWindowReducer(windows, existing.windowId),
                windowId: existing.windowId,
                outcome: "focused-existing",
            };
        }
    }
    if (windows.length >= MAX_WINDOWS) {
        return { windows, windowId: null, outcome: "cap-reached" };
    }
    // Minimize oldest visible windows first so the new one stays open.
    return {
        windows: [...enforceVisibleCap(windows, MAX_VISIBLE_WINDOWS - 1), descriptor],
        windowId: descriptor.windowId,
        outcome: "opened",
    };
};

export const setWindowStateReducer = (
    windows: ComposeWindowDescriptor[],
    windowId: string,
    state: ComposeWindowDisplayState,
): ComposeWindowDescriptor[] => {
    let next = windows.map((window) =>
        window.windowId === windowId ? { ...window, state } : window
    );
    // The expanded window is a centered overlay: only one at a time.
    if (state === "expanded") {
        next = next.map((window) =>
            window.windowId !== windowId && window.state === "expanded"
                ? { ...window, state: "open" as const }
                : window
        );
    }
    if (state !== "minimized") next = enforceVisibleCap(next);
    return next;
};

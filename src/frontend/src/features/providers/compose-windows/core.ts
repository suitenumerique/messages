import { ComposeWindowDescriptor, ComposeWindowPresentation } from "./types";

/** Hard cap on simultaneously tracked windows (expanded + minimized). */
export const MAX_WINDOWS = 8;

/**
 * At most one window is expanded (un-minimized) at a time; the array is
 * MRU-ordered, so when several claim to be expanded the last one wins.
 * Used to sanitize restored payloads.
 */
export const enforceSingleExpanded = (windows: ComposeWindowDescriptor[]): ComposeWindowDescriptor[] => {
    if (windows.filter((window) => !window.isMinimized).length <= 1) return windows;
    const lastExpanded = windows.findLast((window) => !window.isMinimized);
    return windows.map((window) =>
        window.isMinimized || window.windowId === lastExpanded?.windowId
            ? window
            : { ...window, isMinimized: true }
    );
};

export const minimizeWindowReducer = (windows: ComposeWindowDescriptor[], windowId: string): ComposeWindowDescriptor[] => {
    const target = windows.find((window) => window.windowId === windowId);
    if (!target || target.isMinimized) return windows;
    return windows.map((window) =>
        window.windowId === windowId ? { ...window, isMinimized: true } : window
    );
};

/**
 * Expands the window with its remembered presentation and minimizes the
 * others, all in place: a tab opens right where its collapsed self sat.
 * `moveToEnd` relocates it to the end of the list first — for windows
 * emerging from the "+X" overflow, which had no visible slot to keep.
 */
export const restoreWindowReducer = (
    windows: ComposeWindowDescriptor[],
    windowId: string,
    options?: { moveToEnd?: boolean },
): ComposeWindowDescriptor[] => {
    const target = windows.find((window) => window.windowId === windowId);
    if (!target) return windows;
    const restored = { ...target, isMinimized: false };
    const others = windows
        .filter((window) => window.windowId !== windowId)
        .map((window) => (window.isMinimized ? window : { ...window, isMinimized: true }));
    if (options?.moveToEnd) return [...others, restored];
    return windows.map((window) =>
        window.windowId === windowId
            ? restored
            : (window.isMinimized ? window : { ...window, isMinimized: true })
    );
};

/** Restores the window and bumps its focus counter. */
export const focusWindowReducer = (
    windows: ComposeWindowDescriptor[],
    windowId: string,
    options?: { moveToEnd?: boolean },
): ComposeWindowDescriptor[] =>
    restoreWindowReducer(windows, windowId, options).map((window) =>
        window.windowId === windowId
            ? { ...window, focusTick: window.focusTick + 1 }
            : window
    );

/** Changes how the expanded window renders; expands it first if needed. */
export const setPresentationReducer = (
    windows: ComposeWindowDescriptor[],
    windowId: string,
    presentation: ComposeWindowPresentation,
): ComposeWindowDescriptor[] =>
    restoreWindowReducer(windows, windowId).map((window) =>
        window.windowId === windowId ? { ...window, presentation } : window
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
    // Asking again for a composition that is already around should focus it,
    // not stack a second window: a blank untitled "new" window, or a not yet
    // materialized reply/forward on the same parent message (whose subject
    // is auto-filled, hence no title condition there).
    if (!descriptor.draftId) {
        const duplicate = windows.find((window) =>
            !window.draftId
            && window.mode === descriptor.mode
            && window.parentMessageId === descriptor.parentMessageId
            && (descriptor.mode !== "new" || !window.title?.trim())
        );
        if (duplicate) {
            return {
                windows: focusWindowReducer(windows, duplicate.windowId),
                windowId: duplicate.windowId,
                outcome: "focused-existing",
            };
        }
    }
    if (windows.length >= MAX_WINDOWS) {
        return { windows, windowId: null, outcome: "cap-reached" };
    }
    // Minimize the current expanded window so the new one takes its place.
    const minimized = windows.map((window) =>
        window.isMinimized ? window : { ...window, isMinimized: true }
    );
    return {
        windows: [...minimized, descriptor],
        windowId: descriptor.windowId,
        outcome: "opened",
    };
};

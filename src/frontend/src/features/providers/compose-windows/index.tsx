import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { getMailboxThreadsListQueryKeyPrefix } from "@/features/providers/mailbox-cache";
import { getThreadsStatsQueryKey } from "@/features/providers/mailbox";
import { subscribeToComposeBroadcast } from "./broadcast";
import { focusWindowReducer, openWindowReducer, setWindowStateReducer } from "./core";
import { loadPersistedWindows, persistWindows } from "./persistence";
import { ComposeWindowDescriptor, ComposeWindowDisplayState, OpenComposeWindowInput } from "./types";

export { MAX_VISIBLE_WINDOWS, MAX_WINDOWS } from "./core";

type ComposeWindowsContextType = {
    windows: readonly ComposeWindowDescriptor[];
    /**
     * Opens a compose window. Deduplicates on draftId: if the draft is already
     * open in a window, that window is focused instead. Returns the windowId,
     * or null when the hard cap is reached.
     */
    openComposeWindow: (input: OpenComposeWindowInput) => string | null;
    /** Removes the window from the store. No confirmation logic here. */
    closeWindow: (windowId: string) => void;
    setWindowState: (windowId: string, state: ComposeWindowDisplayState) => void;
    updateWindow: (windowId: string, patch: Partial<Pick<ComposeWindowDescriptor, "draftId" | "title">>) => void;
    getWindowByDraftId: (draftId: string) => ComposeWindowDescriptor | undefined;
    /** Restores the window if minimized and asks it to grab focus. */
    focusWindow: (windowId: string) => void;
};

const ComposeWindowsContext = createContext<ComposeWindowsContextType | undefined>(undefined);

export const ComposeWindowsProvider = ({ children }: PropsWithChildren) => {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    // Restore the windows persisted by a previous session; their drafts are
    // reloaded from the API by each window (a deleted draft removes itself).
    const [windows, setWindows] = useState<ComposeWindowDescriptor[]>(loadPersistedWindows);

    // Persist on every change, debounced: subject keystrokes update titles.
    useEffect(() => {
        const timeoutId = setTimeout(() => persistWindows(windows), 300);
        return () => clearTimeout(timeoutId);
    }, [windows]);

    // A pop-out tab edits drafts this tab cannot see changing (queries do not
    // refetch on focus): refresh the affected caches on its broadcasts, and
    // drop any local window whose draft was sent or deleted there.
    useEffect(() => subscribeToComposeBroadcast((message) => {
        if (message.threadId) {
            queryClient.invalidateQueries({ queryKey: ["messages", message.threadId] });
        }
        queryClient.invalidateQueries({ queryKey: getMailboxThreadsListQueryKeyPrefix(message.mailboxId) });
        queryClient.invalidateQueries({ queryKey: getThreadsStatsQueryKey(message.mailboxId) });
        if (message.type === "draft-sent" || message.type === "draft-deleted") {
            setWindows((prev) => prev.filter((window) => window.draftId !== message.draftId));
        }
    }), [queryClient]);

    const focusWindow = useCallback((windowId: string) => {
        setWindows((prev) => focusWindowReducer(prev, windowId));
    }, []);

    const openComposeWindow = useCallback((input: OpenComposeWindowInput): string | null => {
        const descriptor: ComposeWindowDescriptor = {
            windowId: crypto.randomUUID(),
            mailboxId: input.mailboxId,
            mode: input.mode ?? "new",
            state: "open",
            draftId: input.draftId,
            threadId: input.threadId,
            parentMessageId: input.parentMessageId,
            openedOnExistingDraft: !!input.draftId,
            focusTick: 0,
            initialDraft: input.initialDraft,
            initialParent: input.initialParent,
        };
        const { windows: next, windowId, outcome } = openWindowReducer(windows, descriptor);
        if (outcome === "cap-reached") {
            addToast(
                <ToasterItem type="info">
                    <span>{t("Too many compose windows are open. Close one before opening a new one.")}</span>
                </ToasterItem>
            );
        }
        setWindows(next);
        return windowId;
    }, [windows, t]);

    const closeWindow = useCallback((windowId: string) => {
        setWindows((prev) => prev.filter((window) => window.windowId !== windowId));
    }, []);

    const setWindowState = useCallback((windowId: string, state: ComposeWindowDisplayState) => {
        setWindows((prev) => setWindowStateReducer(prev, windowId, state));
    }, []);

    const updateWindow = useCallback((windowId: string, patch: Partial<Pick<ComposeWindowDescriptor, "draftId" | "title">>) => {
        setWindows((prev) => {
            const target = prev.find((window) => window.windowId === windowId);
            if (!target) return prev;
            // Avoid re-rendering every window on each identical notification
            // (e.g. subject watcher firing with an unchanged value).
            const isNoop = Object.entries(patch).every(([key, value]) => target[key as keyof typeof patch] === value);
            if (isNoop) return prev;
            return prev.map((window) =>
                window.windowId === windowId ? { ...window, ...patch } : window
            );
        });
    }, []);

    const getWindowByDraftId = useCallback(
        (draftId: string) => windows.find((window) => window.draftId === draftId),
        [windows]
    );

    const value = useMemo(() => ({
        windows,
        openComposeWindow,
        closeWindow,
        setWindowState,
        updateWindow,
        getWindowByDraftId,
        focusWindow,
    }), [windows, openComposeWindow, closeWindow, setWindowState, updateWindow, getWindowByDraftId, focusWindow]);

    return (
        <ComposeWindowsContext.Provider value={value}>
            {children}
        </ComposeWindowsContext.Provider>
    );
};

export const useComposeWindows = () => {
    const context = useContext(ComposeWindowsContext);
    if (!context) {
        throw new Error("useComposeWindows must be used within a ComposeWindowsProvider");
    }
    return context;
};

/**
 * Same as useComposeWindows but usable outside the provider (e.g. surfaces
 * shared with the standalone pop-out page): returns undefined instead of
 * throwing.
 */
export const useOptionalComposeWindows = () => useContext(ComposeWindowsContext);

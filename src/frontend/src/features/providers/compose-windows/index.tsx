import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { getMailboxThreadsListQueryKeyPrefix } from "@/features/providers/mailbox-cache";
import { getThreadsStatsQueryKey } from "@/features/providers/mailbox";
import { randomUUID } from "@/features/utils/uuid";
import { subscribeToComposeBroadcast } from "./broadcast";
import { focusWindowReducer, minimizeWindowReducer, openWindowReducer, restoreWindowReducer, setPresentationReducer } from "./core";
import { loadPersistedWindows, persistWindows } from "./persistence";
import { ComposeWindowDescriptor, ComposeWindowPresentation, OpenComposeWindowInput } from "./types";

type ComposeWindowsContextType = {
    windows: readonly ComposeWindowDescriptor[];
    /** The single un-minimized window, if any. */
    activeWindow: ComposeWindowDescriptor | undefined;
    /**
     * Opens a compose window. Deduplicates on draftId: if the draft is already
     * open in a window, that window is focused instead. Returns the windowId,
     * or null when the hard cap is reached.
     */
    openComposeWindow: (input: OpenComposeWindowInput) => string | null;
    /** Removes the window from the store. No confirmation logic here. */
    closeWindow: (windowId: string) => void;
    /** Collapses the window into a dock tab. */
    minimizeWindow: (windowId: string) => void;
    /** Expands the window with its remembered presentation, minimizing the others. */
    restoreWindow: (windowId: string) => void;
    /** Changes how the expanded window renders; expands it first if needed. */
    setPresentation: (windowId: string, presentation: ComposeWindowPresentation) => void;
    updateWindow: (windowId: string, patch: Partial<Pick<ComposeWindowDescriptor, "draftId" | "title">>) => void;
    getWindowByDraftId: (draftId: string) => ComposeWindowDescriptor | undefined;
    /** Restores the window if minimized and asks it to grab focus.
     * `moveToEnd` relocates it to the right end of the dock (used when it
     * comes out of the "+X" overflow, which had no visible slot). */
    focusWindow: (windowId: string, options?: { moveToEnd?: boolean }) => void;
    /**
     * Registers the window's form-bound behaviors (close flow, dirtiness),
     * so surfaces outside the window — the mobile overview, the recycling
     * check below — can reach them. Returns the unregister cleanup.
     */
    registerWindowHandle: (windowId: string, handle: ComposeWindowHandle) => () => void;
    /** Runs the window's close flow (save/confirm/discard). */
    requestCloseWindow: (windowId: string) => void;
};

export type ComposeWindowHandle = {
    requestClose: () => void | Promise<void>;
    /** Whether the user touched the form since the window opened: a draft
     * save happened (saves only trigger on user-caused dirtiness) or dirty
     * fields are pending. Automatic signature application does not count. */
    wasUserEdited: () => boolean;
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

    const focusWindow = useCallback((windowId: string, options?: { moveToEnd?: boolean }) => {
        setWindows((prev) => focusWindowReducer(prev, windowId, options));
    }, []);

    // Form-bound behaviors keyed by windowId; a ref because handles must be
    // readable synchronously from openComposeWindow without re-rendering.
    const windowHandlesRef = useRef(new Map<string, ComposeWindowHandle>());
    const registerWindowHandle = useCallback((windowId: string, handle: ComposeWindowHandle) => {
        windowHandlesRef.current.set(windowId, handle);
        return () => {
            windowHandlesRef.current.delete(windowId);
        };
    }, []);
    const requestCloseWindow = useCallback((windowId: string) => {
        void windowHandlesRef.current.get(windowId)?.requestClose();
    }, []);

    const openComposeWindow = useCallback((input: OpenComposeWindowInput): string | null => {
        const descriptor: ComposeWindowDescriptor = {
            windowId: randomUUID(),
            mailboxId: input.mailboxId,
            mode: input.mode ?? "new",
            presentation: "docked",
            isMinimized: false,
            draftId: input.draftId,
            threadId: input.threadId,
            parentMessageId: input.parentMessageId,
            openedOnExistingDraft: !!input.draftId,
            // Ask the window to grab focus (the composer) as soon as it can.
            focusTick: 1,
        };
        // Recycle a merely-consulted draft window: opening another draft
        // while the active window shows an existing draft the user never
        // touched replaces it instead of stacking one more tab. Pristine by
        // definition, it closes without any flush.
        let current = windows;
        if (input.draftId) {
            const active = windows.find((window) => !window.isMinimized);
            if (
                active
                && active.openedOnExistingDraft
                && active.draftId
                && active.draftId !== input.draftId
                && !(windowHandlesRef.current.get(active.windowId)?.wasUserEdited() ?? true)
            ) {
                current = windows.filter((window) => window.windowId !== active.windowId);
            }
        }
        const { windows: next, windowId, outcome } = openWindowReducer(current, descriptor);
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

    const minimizeWindow = useCallback((windowId: string) => {
        setWindows((prev) => minimizeWindowReducer(prev, windowId));
    }, []);

    const restoreWindow = useCallback((windowId: string) => {
        setWindows((prev) => restoreWindowReducer(prev, windowId));
    }, []);

    const setPresentation = useCallback((windowId: string, presentation: ComposeWindowPresentation) => {
        setWindows((prev) => setPresentationReducer(prev, windowId, presentation));
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

    const activeWindow = useMemo(() => windows.find((window) => !window.isMinimized), [windows]);

    const value = useMemo(() => ({
        windows,
        activeWindow,
        openComposeWindow,
        closeWindow,
        minimizeWindow,
        restoreWindow,
        setPresentation,
        updateWindow,
        getWindowByDraftId,
        focusWindow,
        registerWindowHandle,
        requestCloseWindow,
    }), [windows, activeWindow, openComposeWindow, closeWindow, minimizeWindow, restoreWindow, setPresentation, updateWindow, getWindowByDraftId, focusWindow, registerWindowHandle, requestCloseWindow]);

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

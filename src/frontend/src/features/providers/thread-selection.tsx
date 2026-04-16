import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMailboxContext } from "./mailbox";
import { Thread } from "@/features/api/gen/models/thread";

export enum SelectionReadStatus {
    NONE = 'none',
    READ = 'read',
    UNREAD = 'unread',
    MIXED = 'mixed',
}
export enum SelectionStarredStatus {
    NONE = 'none',
    STARRED = 'starred',
    UNSTARRED = 'unstarred',
    MIXED = 'mixed',
}

interface ThreadSelectionState {
    selectedThreadIds: Set<string>;
    isSelectionMode: boolean;
    toggleThreadSelection: (
        threadId: string,
        shiftKey?: boolean,
        ctrlKey?: boolean,
        arrowUpKey?: 'up' | 'down'
    ) => void;
    selectAllThreads: () => void;
    clearSelection: () => void;
    enableSelectionMode: () => void;
    isAllSelected: boolean;
    isSomeSelected: boolean;
    selectionReadStatus: SelectionReadStatus;
    selectionStarredStatus: SelectionStarredStatus;
}

const ThreadSelectionContext = createContext<ThreadSelectionState | null>(null);

const useThreadSelectionState = (threads: Thread[] | undefined, selectedThread: Thread | null | undefined): ThreadSelectionState => {
    const searchParams = useSearchParams();
    const [selectedThreadIds, setSelectedThreadIds] = useState<Set<string>>(new Set());
    const [isSelectionMode, setIsSelectionMode] = useState(false);
    const lastActiveThreadIdRef = useRef<string | null>(null);
    const anchorThreadIdRef = useRef<string | null>(null);
    const focusThreadIdRef = useRef<string | null>(null);

    const toggleThreadSelection = useCallback((
        threadId: string,
        shiftKey: boolean = false,
        ctrlKey: boolean = false,
        arrowUpKey?: 'up' | 'down'
    ) => {
        if (!threads) return;

        setSelectedThreadIds((prev) => {
            let newSet: Set<string>;

            if (shiftKey && arrowUpKey) {
                // Shift+Arrow key: macOS Finder-like behavior
                if (anchorThreadIdRef.current === null || focusThreadIdRef.current === null) {
                    if (prev.size > 0) {
                        const firstSelectedId = Array.from(prev)[0];
                        anchorThreadIdRef.current = firstSelectedId;
                        focusThreadIdRef.current = firstSelectedId;
                    } else {
                        anchorThreadIdRef.current = threadId;
                        focusThreadIdRef.current = threadId;
                    }
                }

                const currentFocusIndex = threads.findIndex((t) => t.id === focusThreadIdRef.current);
                if (currentFocusIndex === -1) {
                    // Focused thread was removed from list, reset to current thread
                    focusThreadIdRef.current = threadId;
                    anchorThreadIdRef.current = threadId;
                    newSet = new Set([threadId]);
                } else {
                    let newFocusIndex = currentFocusIndex;
                    if (arrowUpKey === 'up' && newFocusIndex > 0) {
                        newFocusIndex = newFocusIndex - 1;
                    } else if (arrowUpKey === 'down' && newFocusIndex < threads.length - 1) {
                        newFocusIndex = newFocusIndex + 1;
                    }

                    const newFocusThreadId = threads[newFocusIndex].id;
                    focusThreadIdRef.current = newFocusThreadId;

                    const anchorIndex = threads.findIndex((t) => t.id === anchorThreadIdRef.current);
                    const effectiveAnchorIndex = anchorIndex !== -1 ? anchorIndex : newFocusIndex;
                    const start = Math.min(effectiveAnchorIndex, newFocusIndex);
                    const end = Math.max(effectiveAnchorIndex, newFocusIndex);
                    const range = threads.slice(start, end + 1);
                    newSet = new Set(range.map((thread) => thread.id));

                    setTimeout(() => {
                        const threadItem = document.querySelector<HTMLElement>(`[data-thread-id="${newFocusThreadId}"]`);
                        threadItem?.focus();
                    }, 0);
                }
            }
            else if (shiftKey) {
                // Shift+Click: range selection
                const index = threads.findIndex((t) => t.id === threadId);
                let anchorIndex: number;

                if (lastActiveThreadIdRef.current !== null) {
                    const foundIndex = threads.findIndex((t) => t.id === lastActiveThreadIdRef.current);
                    anchorIndex = foundIndex !== -1 ? foundIndex : index;
                } else if (selectedThread) {
                    const activeThreadIndex = threads.findIndex((t) => t.id === selectedThread.id);
                    anchorIndex = activeThreadIndex !== -1 ? activeThreadIndex : index;
                    lastActiveThreadIdRef.current = selectedThread.id;
                } else {
                    anchorIndex = index;
                    lastActiveThreadIdRef.current = threadId;
                }

                anchorThreadIdRef.current = threads[anchorIndex]?.id ?? null;
                focusThreadIdRef.current = threadId;

                const start = Math.min(anchorIndex, index);
                const end = Math.max(anchorIndex, index);
                const range = threads.slice(start, end + 1);
                newSet = new Set(range.map((thread) => thread.id));
            } else if (ctrlKey) {
                // Ctrl/Cmd+Click: toggle individual without affecting others
                newSet = new Set(prev);
                if (newSet.has(threadId)) {
                    newSet.delete(threadId);
                } else {
                    newSet.add(threadId);
                }
                lastActiveThreadIdRef.current = threadId;
                focusThreadIdRef.current = threadId;
            } else {
                // Normal click: if already selected, unselect it; otherwise, clear others and select only this one
                if (prev.has(threadId)) {
                    newSet = new Set(prev);
                    newSet.delete(threadId);
                } else {
                    newSet = new Set([threadId]);
                }
                lastActiveThreadIdRef.current = threadId;
                anchorThreadIdRef.current = threadId;
                focusThreadIdRef.current = threadId;
            }

            if (newSet.size > 0) {
                setIsSelectionMode(true);
            }

            return newSet;
        });
    }, [threads, selectedThread]);

    const selectAllThreads = useCallback(() => {
        if (!threads) return;
        const allIds = new Set(threads.map((thread) => thread.id));
        setSelectedThreadIds(allIds);
        setIsSelectionMode(true);
    }, [threads]);

    const clearSelection = useCallback(() => {
        setSelectedThreadIds(new Set());
        lastActiveThreadIdRef.current = null;
        anchorThreadIdRef.current = null;
        focusThreadIdRef.current = null;
        setIsSelectionMode(false);
    }, []);

    const enableSelectionMode = useCallback(() => {
        setIsSelectionMode(true);
    }, []);

    const isAllSelected = useMemo(() => {
        if (!threads?.length) return false;
        return threads.every((thread) => selectedThreadIds.has(thread.id));
    }, [threads, selectedThreadIds]);

    const isSomeSelected = useMemo(() => {
        if (!threads?.length) return false;
        return threads.some((thread) => selectedThreadIds.has(thread.id));
    }, [threads, selectedThreadIds]);

    const { selectionReadStatus, selectionStarredStatus } = useMemo(() => {
        if (selectedThreadIds.size === 0) return { selectionReadStatus: SelectionReadStatus.NONE, selectionStarredStatus: SelectionStarredStatus.NONE };
        const selectedThreads = threads?.filter(t => selectedThreadIds.has(t.id)) || [];
        if (selectedThreads.length === 0) return { selectionReadStatus: SelectionReadStatus.NONE, selectionStarredStatus: SelectionStarredStatus.NONE };

        const hasUnread = selectedThreads.some(t => t.has_unread);
        const hasRead = selectedThreads.some(t => !t.has_unread);
        const readStatus = hasUnread && hasRead ? SelectionReadStatus.MIXED : hasUnread ? SelectionReadStatus.UNREAD : SelectionReadStatus.READ;

        const hasStarred = selectedThreads.some(t => t.has_starred);
        const hasUnstarred = selectedThreads.some(t => !t.has_starred);
        const starredStatus = hasStarred && hasUnstarred ? SelectionStarredStatus.MIXED : hasStarred ? SelectionStarredStatus.STARRED : SelectionStarredStatus.UNSTARRED;

        return { selectionReadStatus: readStatus, selectionStarredStatus: starredStatus };
    }, [selectedThreadIds, threads]);

    // Prune stale IDs from selection when threads change
    useEffect(() => {
        if (!threads) return;
        setSelectedThreadIds((prev) => {
            if (prev.size === 0) return prev;
            const threadIds = new Set(threads.map((t) => t.id));
            const pruned = new Set([...prev].filter((id) => threadIds.has(id)));
            if (pruned.size === prev.size) return prev;
            if (pruned.size === 0) {
                setIsSelectionMode(false);
            }
            return pruned;
        });
    }, [threads]);

    // Clear selection when search params change
    useEffect(() => {
        setSelectedThreadIds(new Set());
        lastActiveThreadIdRef.current = null;
        anchorThreadIdRef.current = null;
        focusThreadIdRef.current = null;
        setIsSelectionMode(false);
    }, [searchParams]);

    // Keyboard controls
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            const isSelectAllShortcut = (e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'a');

            if (isSelectAllShortcut) {
                const threadPanel = document.querySelector('.thread-panel');
                const isFocusInPanel = threadPanel && threadPanel.contains(document.activeElement);

                if (isSelectionMode || isFocusInPanel) {
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation();
                    selectAllThreads();
                    return;
                }
            }

            if (!isSelectionMode) return;

            if (e.key === 'Escape') {
                // Defer to any modal dialog / popup stacked above (labels widget popup,
                // Cunningham modal, etc.) — they own the Escape while open.
                if (document.querySelector('[aria-modal="true"]')) return;
                e.preventDefault();
                clearSelection();
                return;
            }
        };

        document.addEventListener('keydown', handleKeyDown, true);

        return () => {
            document.removeEventListener('keydown', handleKeyDown, true);
        };
    }, [selectedThreadIds.size, isSelectionMode, clearSelection, selectAllThreads]);

    return {
        selectedThreadIds,
        isSelectionMode,
        toggleThreadSelection,
        selectAllThreads,
        clearSelection,
        enableSelectionMode,
        isAllSelected,
        isSomeSelected,
        selectionReadStatus,
        selectionStarredStatus,
    };
};

export const ThreadSelectionProvider = ({ children }: PropsWithChildren) => {
    const { threads, selectedThread } = useMailboxContext();
    const selection = useThreadSelectionState(threads?.results, selectedThread);

    return (
        <ThreadSelectionContext.Provider value={selection}>
            {children}
        </ThreadSelectionContext.Provider>
    );
};

export const useThreadSelection = () => {
    const context = useContext(ThreadSelectionContext);
    if (!context) {
        throw new Error("useThreadSelection must be used within a ThreadSelectionProvider");
    }
    return context;
};

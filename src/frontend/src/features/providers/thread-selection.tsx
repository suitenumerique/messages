import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useMailboxContext } from "./mailbox";
import { Thread } from "@/features/api/gen/models/thread";
import { computeRange, computeToggle, pruneSelection, resolveAnchorIndex } from "./thread-selection-core";

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
    toggleThread: (threadId: string) => void;
    selectRange: (threadId: string, fallbackAnchorId?: string) => void;
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
    const searchParams = useUrlSearchParams();
    const [selectedThreadIds, setSelectedThreadIds] = useState<Set<string>>(new Set());
    const [isSelectionMode, setIsSelectionMode] = useState(false);
    const anchorThreadIdRef = useRef<string | null>(null);

    /**
     * Additively toggle a thread in/out of the selection. The toggled
     * thread becomes the anchor for subsequent range selections.
     * Selection mode stays on even when the selection empties, so the
     * bulk-action header does not flicker mid-interaction.
     */
    const toggleThread = useCallback((threadId: string) => {
        setSelectedThreadIds((prev) => computeToggle(prev, threadId));
        anchorThreadIdRef.current = threadId;
        setIsSelectionMode(true);
    }, []);

    /**
     * Select the range between the current anchor and the given thread.
     * Successive range selections pivot from the same anchor.
     * @param fallbackAnchorId seeds the anchor when none is set (e.g. the
     * previously focused thread during Shift+Arrow keyboard expansion)
     */
    const selectRange = useCallback((threadId: string, fallbackAnchorId?: string) => {
        if (!threads) return;
        const targetIndex = threads.findIndex((thread) => thread.id === threadId);
        if (targetIndex === -1) return;

        const anchorIndex = resolveAnchorIndex(
            threads,
            targetIndex,
            selectedThreadIds.size > 0 ? anchorThreadIdRef.current : null,
            fallbackAnchorId,
            selectedThread?.id,
        );
        anchorThreadIdRef.current = threads[anchorIndex].id;
        setSelectedThreadIds(computeRange(threads, anchorIndex, targetIndex));
        setIsSelectionMode(true);
    }, [threads, selectedThread, selectedThreadIds.size]);

    const selectAllThreads = useCallback(() => {
        if (!threads) return;
        const allIds = new Set(threads.map((thread) => thread.id));
        setSelectedThreadIds(allIds);
        setIsSelectionMode(true);
    }, [threads]);

    const clearSelection = useCallback(() => {
        setSelectedThreadIds(new Set());
        anchorThreadIdRef.current = null;
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
            const pruned = pruneSelection(prev, threads);
            if (pruned !== prev && pruned.size === 0) {
                setIsSelectionMode(false);
            }
            return pruned;
        });
    }, [threads]);

    // Clear selection when search params change
    useEffect(() => {
        setSelectedThreadIds(new Set());
        anchorThreadIdRef.current = null;
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
    }, [isSelectionMode, clearSelection, selectAllThreads]);

    return {
        selectedThreadIds,
        isSelectionMode,
        toggleThread,
        selectRange,
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

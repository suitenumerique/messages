/**
 * Pure selection logic for the thread multi-selection feature.
 *
 * These helpers are framework-free so the selection semantics (additive
 * toggle, range anchoring, pruning) can be unit-tested without React.
 */

type ThreadRef = { id: string };

/**
 * Additively toggle a thread in/out of the selection without affecting
 * the other selected threads.
 * @returns a new Set with the thread added or removed
 */
export const computeToggle = (prev: Set<string>, threadId: string): Set<string> => {
    const next = new Set(prev);
    if (next.has(threadId)) {
        next.delete(threadId);
    } else {
        next.add(threadId);
    }
    return next;
};

/**
 * Resolve the index of the range-selection anchor.
 *
 * Resolution order: the current anchor (if still in the list), then the
 * provided fallback (e.g. the previously focused thread), then the thread
 * currently opened in the view, then the target itself.
 *
 * @param threads ordered list of visible threads
 * @param targetIndex index of the thread the range extends to
 * @param anchorId id of the current selection anchor, if any
 * @param fallbackAnchorId id used to seed the anchor when none is set
 * @param openThreadId id of the thread opened in the thread view, if any
 * @returns the index to anchor the range on
 */
export const resolveAnchorIndex = (
    threads: ThreadRef[],
    targetIndex: number,
    anchorId: string | null,
    fallbackAnchorId?: string,
    openThreadId?: string,
): number => {
    for (const candidate of [anchorId, fallbackAnchorId, openThreadId]) {
        if (!candidate) continue;
        const index = threads.findIndex((thread) => thread.id === candidate);
        if (index !== -1) return index;
    }
    return targetIndex;
};

/**
 * Build the selection covering the inclusive range between two indices.
 * @returns a new Set containing exactly the threads in the range
 */
export const computeRange = (
    threads: ThreadRef[],
    anchorIndex: number,
    targetIndex: number,
): Set<string> => {
    const start = Math.min(anchorIndex, targetIndex);
    const end = Math.max(anchorIndex, targetIndex);
    return new Set(threads.slice(start, end + 1).map((thread) => thread.id));
};

/**
 * Drop selected ids that no longer exist in the thread list.
 * @returns the same Set reference when nothing changed (to avoid re-renders)
 */
export const pruneSelection = (prev: Set<string>, threads: ThreadRef[]): Set<string> => {
    if (prev.size === 0) return prev;
    const threadIds = new Set(threads.map((thread) => thread.id));
    const pruned = new Set([...prev].filter((id) => threadIds.has(id)));
    return pruned.size === prev.size ? prev : pruned;
};

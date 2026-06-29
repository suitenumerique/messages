import { useCallback, useEffect, useRef } from "react";
import { Thread } from "@/features/api/gen/models/thread";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { useThreadListboxFocus } from "@/features/providers/thread-listbox-focus";
import { getNextFocusId, isListboxNavKey } from "./listbox-navigation";

export type ThreadListboxItemProps = {
    tabIndex: 0 | -1;
    itemRef: (node: HTMLAnchorElement | null) => void;
    onFocusItem: () => void;
};

/**
 * Owns keyboard focus for the thread listbox (the selection provider owns
 * what is selected): roving tabindex, arrow/Home/End navigation, Space
 * toggle and Shift+Arrow range expansion. Enter needs no handling here —
 * the focused anchor fires a native click (detail === 0) that the item's
 * click handler lets through to navigation.
 *
 * The focus state itself lives in ThreadListboxFocusProvider because the
 * ThreadPanel (and this hook with it) is remounted on route transitions;
 * only the DOM refs map is local, it is rebuilt on every mount.
 */
export const useThreadListbox = (threads: Thread[] | undefined) => {
    const { toggleThread, selectRange } = useThreadSelection();
    const { focusedThreadId, setFocusedThreadId, ownsFocusRef, lastFocusedIndexRef } = useThreadListboxFocus();
    const itemRefs = useRef(new Map<string, HTMLAnchorElement>());

    const firstThreadId = threads?.[0]?.id ?? null;

    const getItemProps = useCallback((threadId: string): ThreadListboxItemProps => ({
        // Roving tabindex: a single tab stop, falling back to the first
        // thread while nothing has been focused yet.
        tabIndex: (focusedThreadId ?? firstThreadId) === threadId ? 0 : -1,
        itemRef: (node: HTMLAnchorElement | null) => {
            if (node) {
                itemRefs.current.set(threadId, node);
            } else {
                itemRefs.current.delete(threadId);
            }
        },
        // Syncs state when focus arrives via Tab or click, and aligns DOM
        // focus on the option anchor: Safari does not focus anchors on
        // pointer click, and Chrome focuses the (aria-hidden) checkbox
        // input on mousedown — both would leave Arrow/Home/End dead even
        // though the roving state points at this item. The guard makes the
        // call idempotent when invoked from the anchor's own focus event.
        onFocusItem: () => {
            ownsFocusRef.current = true;
            setFocusedThreadId(threadId);
            const node = itemRefs.current.get(threadId);
            if (node && document.activeElement !== node) {
                node.focus({ preventScroll: true });
            }
        },
    }), [focusedThreadId, firstThreadId, setFocusedThreadId, ownsFocusRef]);

    // The list stops owning focus when it explicitly moves elsewhere. A blur
    // with no relatedTarget (click on a non-focusable area) counts too —
    // crucially, browsers fire NO blur at all when the focused node is
    // unmounted, moved or recreated, which is how silent focus loss is told
    // apart from a deliberate focus change.
    const onBlur = useCallback((e: React.FocusEvent<HTMLElement>) => {
        if (!(e.relatedTarget instanceof Node) || !e.currentTarget.contains(e.relatedTarget)) {
            ownsFocusRef.current = false;
        }
    }, [ownsFocusRef]);

    // Restore focus dropped on <body> when the focused item's node was
    // unmounted, moved or recreated: list reorders (mark-as-read patch,
    // pinned-threads merge) and full ThreadPanel remounts on route
    // transitions. Without this, keyboard navigation dies after opening a
    // thread. preventScroll: useScrollRestore already restores the list
    // scroll position on remount, a focus-triggered scroll would fight it.
    useEffect(() => {
        if (!ownsFocusRef.current) return;
        if (document.activeElement !== document.body) return;
        const targetId = focusedThreadId ?? firstThreadId;
        if (!targetId) return;
        itemRefs.current.get(targetId)?.focus({ preventScroll: true });
    });

    const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
        if (!threads?.length) return;

        if (e.key === ' ') {
            if (!focusedThreadId) return;
            // Prevent page scroll on Space
            e.preventDefault();
            toggleThread(focusedThreadId);
            return;
        }

        if (!isListboxNavKey(e.key) || e.ctrlKey || e.metaKey || e.altKey) return;

        const nextId = getNextFocusId(threads, focusedThreadId, e.key);
        if (!nextId) return;
        e.preventDefault();

        const previousFocusId = focusedThreadId ?? undefined;
        setFocusedThreadId(nextId);
        const node = itemRefs.current.get(nextId);
        node?.focus({ preventScroll: true });
        node?.scrollIntoView({ block: 'nearest' });

        if (e.shiftKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
            // Finder-like expansion: the previous focus seeds the anchor
            // when no anchor exists yet.
            selectRange(nextId, previousFocusId);
        }
    }, [threads, focusedThreadId, toggleThread, selectRange, setFocusedThreadId]);

    // Keep the focused thread valid across refetch/prune: when it leaves
    // the list, clamp to the same index. The restore effect above takes
    // care of re-anchoring DOM focus after the state settles.
    useEffect(() => {
        if (!threads || focusedThreadId === null) return;

        const index = threads.findIndex((thread) => thread.id === focusedThreadId);
        if (index !== -1) {
            lastFocusedIndexRef.current = index;
            return;
        }

        if (threads.length === 0) {
            setFocusedThreadId(null);
            return;
        }

        const clampedIndex = Math.min(lastFocusedIndexRef.current, threads.length - 1);
        setFocusedThreadId(threads[clampedIndex].id);
    }, [threads, focusedThreadId, setFocusedThreadId, lastFocusedIndexRef]);

    return { focusedThreadId, getItemProps, onKeyDown, onBlur };
};

import { RefObject, useEffect, useRef } from 'react';

type ElementRefs = Record<string, HTMLElement | null>;

type UseVisibilityObserverOptions = {
    /** Disable the observer entirely (e.g. while the parent view is not ready). */
    enabled: boolean;
    /** IDs of the elements to watch — used to keep the observer in sync with the rendered DOM. */
    ids: readonly string[];
    /** Mutable record holding `id → DOM element` references populated by the parent. */
    refs: RefObject<ElementRefs>;
    /** Scroll container used as the IntersectionObserver root. */
    rootRef: RefObject<HTMLElement | null>;
    /** Optional top offset subtracted from the top of the viewport. */
    topOffset?: number;
    /** Called for each element that crosses into view. */
    onVisible: (entry: IntersectionObserverEntry) => void;
};

/**
 * Watches a list of DOM elements and fires `onVisible` whenever one of them
 * scrolls into view of `rootRef`. Encapsulates the IntersectionObserver setup,
 * sticky-header offset and lifecycle that would otherwise be duplicated by
 * each scroll-to-acknowledge feature (mark-as-read, mention acknowledgment…).
 *
 * `onVisible` does not need to be memoized: it is captured through a ref so
 * that updates do not tear down the observer.
 */
export function useVisibilityObserver({
    enabled,
    ids,
    refs,
    rootRef,
    topOffset = 0,
    onVisible,
}: UseVisibilityObserverOptions): void {
    // Stable callback wrapper — keeps the observer alive across renders even
    // when the parent passes an inline arrow function.
    const onVisibleRef = useRef(onVisible);
    useEffect(() => {
        onVisibleRef.current = onVisible;
    }, [onVisible]);

    const idsKey = ids.join(',');

    useEffect(() => {
        if (!enabled || ids.length === 0) return;

        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) onVisibleRef.current(entry);
            });
        }, {
            root: rootRef.current,
            rootMargin: `-${topOffset}px 0px 0px 0px`,
        });

        ids.forEach((id) => {
            const el = refs.current[id];
            if (el) observer.observe(el);
        });

        return () => observer.disconnect();
        // `ids` is intentionally tracked through `idsKey` to avoid re-running
        // when the array reference changes but the contents do not.
    }, [enabled, idsKey, topOffset, refs, rootRef]);
}

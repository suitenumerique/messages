import { createContext, PropsWithChildren, useCallback, useContext, useLayoutEffect, useRef } from "react";

type ScrollState = { position: number; contextKey: string };

type ScrollRestoreContextType = {
    getScrollState: (scrollId: string) => ScrollState;
    setScrollState: (scrollId: string, state: ScrollState) => void;
};

const ScrollRestoreContext = createContext<ScrollRestoreContextType | null>(null);

export const ScrollRestoreProvider = ({ children }: PropsWithChildren) => {
    const statesRef = useRef<Record<string, ScrollState>>({});

    const getScrollState = (scrollId: string): ScrollState => {
        return statesRef.current[scrollId] ?? { position: 0, contextKey: '' };
    };

    const setScrollState = (scrollId: string, state: ScrollState) => {
        statesRef.current[scrollId] = state;
    };

    return (
        <ScrollRestoreContext.Provider value={{ getScrollState, setScrollState }}>
            {children}
        </ScrollRestoreContext.Provider>
    );
};

/**
 * Persists and restores scroll position of a container across
 * unmount/remount cycles (e.g. page navigations within the same layout).
 * Must be used within a ScrollRestoreProvider.
 *
 * @param scrollId    A unique identifier for this scroll container.
 * @param contextKey  Identifies the current view (e.g. mailbox + folder).
 *                    Scroll is only restored when the saved contextKey matches.
 * @param deps        Extra dependencies that should trigger a restore attempt
 *                    (e.g. the data rendered inside the container).
 */
export const useScrollRestore = (
    scrollId: string,
    contextKey: string,
    deps: unknown[] = [],
) => {
    const context = useContext(ScrollRestoreContext);
    if (!context) {
        throw new Error("`useScrollRestore` must be used within a `ScrollRestoreProvider`.");
    }
    const { getScrollState, setScrollState } = context;
    const containerRef = useRef<HTMLDivElement>(null);

    const onScroll = useCallback(() => {
        if (containerRef.current) {
            setScrollState(scrollId, {
                position: containerRef.current.scrollTop,
                contextKey,
            });
        }
    }, [scrollId, contextKey, setScrollState]);

    useLayoutEffect(() => {
        const el = containerRef.current;
        if (!el) return;
        const saved = getScrollState(scrollId);
        if (saved.contextKey === contextKey) {
            // Same context: restore saved position (if any)
            if (saved.position > 0 && el.scrollTop === 0) {
                el.scrollTop = saved.position;
            }
        } else {
            // Context changed (e.g. folder switch): reset to top
            setScrollState(scrollId, { position: 0, contextKey });
            el.scrollTop = 0;
        }
    }, [scrollId, contextKey, getScrollState, setScrollState, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps

    return { containerRef, onScroll };
}

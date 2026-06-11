import { createContext, Dispatch, PropsWithChildren, SetStateAction, useContext, useMemo, useRef, useState } from "react";

interface ThreadListboxFocusState {
    focusedThreadId: string | null;
    setFocusedThreadId: Dispatch<SetStateAction<string | null>>;
    /** Whether the listbox currently owns keyboard focus. */
    ownsFocusRef: { current: boolean };
    /** Index of the last focused thread, used to clamp focus after prune. */
    lastFocusedIndexRef: { current: number };
}

const ThreadListboxFocusContext = createContext<ThreadListboxFocusState | null>(null);

/**
 * Holds the thread listbox roving-focus state outside the ThreadPanel
 * component: the panel is remounted on every route transition between
 * "no thread open" and "thread open" (they are two distinct routes each
 * mounting their own ThreadPanel), which would otherwise reset the
 * focused thread and break keyboard navigation after opening a thread.
 */
export const ThreadListboxFocusProvider = ({ children }: PropsWithChildren) => {
    const [focusedThreadId, setFocusedThreadId] = useState<string | null>(null);
    const ownsFocusRef = useRef(false);
    const lastFocusedIndexRef = useRef(0);

    const value = useMemo(() => ({
        focusedThreadId,
        setFocusedThreadId,
        ownsFocusRef,
        lastFocusedIndexRef,
    }), [focusedThreadId]);

    return (
        <ThreadListboxFocusContext.Provider value={value}>
            {children}
        </ThreadListboxFocusContext.Provider>
    );
};

export const useThreadListboxFocus = () => {
    const context = useContext(ThreadListboxFocusContext);
    if (!context) {
        throw new Error("useThreadListboxFocus must be used within a ThreadListboxFocusProvider");
    }
    return context;
};

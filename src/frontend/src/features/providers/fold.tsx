import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useRef, useState } from "react";

type FoldContextType = {
    isFolded: (key: string) => boolean | undefined;
    toggle: (key: string) => void;
    subscribe: (key: string, isFolded?: boolean) => void;
    unsubscribe: (key: string) => void;
    areAllFolded: boolean | undefined;
    toggleAll: () => void;
}

type FoldGlobalHookContext = {
    areAllFolded: boolean | undefined;
    toggleAll: () => void;
}

type FoldHookContext = FoldGlobalHookContext & {
    isFolded: boolean | undefined;
    toggle: () => void;
}

const FoldContext = createContext<FoldContextType | undefined>(undefined);

/**
 * Provider to manage a global fold state in case you need to fold/unfold
 * a group of elements globally.
 * This provider is able to register foldable elements and manage their state
 * individually and also toggle fold state for all registered elements at once.
 */
export const FoldProvider = ({ children }: PropsWithChildren) => {
    const [subscribers, setSubscribers] = useState<Map<string, boolean>>(new Map());

    const subscribe = (id: string, isFolded: boolean = true) => {
        setSubscribers((prev) => {
            if (prev.has(id)) {
                console.warn(`FoldProvider: subscriber with key "${id}" already registered`);
                return prev;
            }
            const newSubscribers = new Map(prev);
            newSubscribers.set(id, isFolded);
            return newSubscribers;
        });
    }

    const unsubscribe = (id: string) => {
        setSubscribers((prev) => {
            const newSubscribers = new Map(prev);
            newSubscribers.delete(id);
            return newSubscribers;
        });
    }

    const isFolded = (id: string) => {
        if (!subscribers.has(id)) {
            console.warn(`FoldProvider: subscriber with key "${id}" not registered`);
            return;
        }
        return subscribers.get(id);
    }

    const areAllFolded = useMemo(() => {
        if (subscribers.size === 0) return undefined;
        return Array.from(subscribers.values()).every((folded) => folded === true)
    }, [subscribers]);

    const toggle = (id: string) => {
        setSubscribers((prev) => {
            if (!prev.has(id)) {
                console.warn(`FoldProvider: subscriber with key "${id}" not registered`);
                return prev;
            }
            const newSubscribers = new Map(prev);
            newSubscribers.set(id, !prev.get(id));
            return newSubscribers;
        });
    }

    const toggleAll = () => {
        setSubscribers((prev) => {
            // Unfold all if all are folded, otherwise fold all
            const state = !areAllFolded;
            const newSubscribers = new Map(Array.from(prev.entries()).map(([id]) => [id, state]));
            return newSubscribers;
        });
    }


    const context = useMemo<FoldContextType>(() => ({
        subscribe,
        unsubscribe,
        isFolded,
        toggle,
        areAllFolded,
        toggleAll,
    }), [subscribe, unsubscribe, isFolded, toggle, areAllFolded, toggleAll, subscribers]);

    return (
        <FoldContext.Provider value={context}>
            {children}
        </FoldContext.Provider>
    )
}

export function useFold(): FoldGlobalHookContext
export function useFold(id: string, isFolded?: boolean | null): FoldHookContext
export function useFold(id?: string, isFolded: boolean | null = true): FoldHookContext | FoldGlobalHookContext {
    const ctx = useContext(FoldContext);
    const isRegistered = useRef(false);

    if (!ctx) {
        throw new Error('useFold must be used within a FoldProvider');
    }

    const context = useMemo<FoldHookContext | FoldGlobalHookContext>(() => ({
        ...(id ? {
            isFolded: isRegistered.current ? ctx.isFolded(id) : undefined,
            toggle: () => ctx.toggle(id),
        } : {}),
        areAllFolded: ctx.areAllFolded,
        toggleAll: ctx.toggleAll,
    }), [id, ctx]);

    useEffect(() => {
        if (id && isFolded !== null) {
            ctx.subscribe(id, isFolded);
            isRegistered.current = true;
            return () => {
                ctx.unsubscribe(id);
                isRegistered.current = false;
            };
        }
    }, [id]);

    return context;
}

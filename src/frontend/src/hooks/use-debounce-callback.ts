import { useCallback, useEffect, useMemo, useRef } from 'react';

/**
 * Debounced callback. Returns the debounced function with a `cancel`
 * method attached so callers can drop any pending invocation — useful
 * when the input that fed the callback has been reset externally and
 * the queued call would carry stale state.
 */
export type DebouncedCallback<Fn extends (...args: any[]) => void> =
    ((...args: Parameters<Fn>) => void) & { cancel: () => void };

export function useDebounceCallback<Fn extends (...args: Parameters<Fn>) => void>(callback: Fn, delay: number): DebouncedCallback<Fn> {
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);

    const cancel = useCallback(() => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
    }, []);

    const debouncedCallback = useCallback((...args: Parameters<Fn>) => {
        cancel();
        timeoutRef.current = setTimeout(() => callback(...args), delay);
    }, [callback, delay, cancel]);

    useEffect(() => cancel, [cancel]);

    return useMemo(() => {
        const fn = debouncedCallback as DebouncedCallback<Fn>;
        fn.cancel = cancel;
        return fn;
    }, [debouncedCallback, cancel]);
}

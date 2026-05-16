import { useEffect, useMemo, useRef } from 'react';

/**
 * Debounced callback. Returns the debounced function with a `cancel`
 * method attached so callers can drop any pending invocation — useful
 * when the input that fed the callback has been reset externally and
 * the queued call would carry stale state.
 */
export type DebouncedCallback<P extends readonly unknown[]> =
    ((...args: P) => void) & { cancel: () => void };

export function useDebounceCallback<P extends readonly unknown[]>(
    callback: (...args: P) => void,
    delay: number,
): DebouncedCallback<P> {
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    // Latest-callback ref so the memoized debounced function always
    // invokes the current callback without losing its own identity
    // when the parent re-renders with a fresh arrow function.
    const callbackRef = useRef(callback);
    callbackRef.current = callback;

    // Clean up any pending timer on unmount so a queued callback can't
    // fire after the host component is gone.
    useEffect(
        () => () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
            }
        },
        [],
    );

    return useMemo<DebouncedCallback<P>>(() => {
        const cancel = () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
                timeoutRef.current = null;
            }
        };
        const fn = ((...args: P) => {
            cancel();
            timeoutRef.current = setTimeout(
                () => callbackRef.current(...args),
                delay,
            );
        }) as DebouncedCallback<P>;
        fn.cancel = cancel;
        return fn;
    }, [delay]);
}

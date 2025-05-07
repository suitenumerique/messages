import { useCallback, useEffect, useRef } from 'react';

/**
 * useDebounceCallback hook
 * Ensure the callback is called only after the delay has passed
 */
export function useDebounceCallback<Fn extends (...args: Parameters<Fn>) => void>(callback: Fn, delay: number): (...args: Parameters<Fn>) => void {
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    const debouncedCallback = useCallback((...args: Parameters<Fn>) => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
        timeoutRef.current = setTimeout(() => callback(...args), delay);
    }, [callback, delay]);

    useEffect(() => () => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
    }, []);

  return debouncedCallback;
} 
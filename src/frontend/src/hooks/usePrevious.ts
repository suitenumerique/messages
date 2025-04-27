import { useEffect, useRef } from 'react';

/**
 * Custom hook that stores and returns the previous value of a variable
 * @param value The value to track
 * @returns The previous value of the tracked variable
 */
function usePrevious<T>(value: T): T | undefined {
  const ref = useRef<T>(value);

  useEffect(() => {
    ref.current = value;
  }, [value]);

  return ref.current;
}

export default usePrevious;
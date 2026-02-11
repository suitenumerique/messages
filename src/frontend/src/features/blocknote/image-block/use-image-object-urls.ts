import { useCallback, useEffect, useRef } from 'react';

interface UseImageObjectUrlsReturn {
  /** Create an Object URL for a file, storing the mapping objectUrl→base64 */
  createObjectUrl: (file: File, base64DataUrl: string) => string;
  /** Replace all Object URLs with their base64 counterparts in a string */
  resolveObjectUrls: (content: string) => string;
}

/**
 * Manages a bidirectional mapping between short Object URLs and large base64
 * data URLs. This allows BlockNote editors to work with lightweight ~60-char
 * Object URLs internally, while resolving them back to base64 only when
 * persisting form values — avoiding expensive string operations on every
 * keystroke.
 */
export const useImageObjectUrls = (): UseImageObjectUrlsReturn => {
  const mapRef = useRef<Map<string, string>>(new Map());

  const createObjectUrl = useCallback(
    (file: File, base64DataUrl: string): string => {
      const objectUrl = URL.createObjectURL(file);
      mapRef.current.set(objectUrl, base64DataUrl);
      return objectUrl;
    },
    [],
  );

  const resolveObjectUrls = useCallback((content: string): string => {
    let resolved = content;
    for (const [objectUrl, base64] of mapRef.current) {
      resolved = resolved.replaceAll(objectUrl, base64);
    }
    return resolved;
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    return () => {
      for (const objectUrl of map.keys()) {
        URL.revokeObjectURL(objectUrl);
      }
      map.clear();
    };
  }, []);

  return { createObjectUrl, resolveObjectUrls };
};

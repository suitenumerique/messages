import { useLocation } from "@tanstack/react-router";
import { useMemo } from "react";

/**
 * Returns a `URLSearchParams` derived from TanStack Router's search string.
 * The returned identity is stable across renders as long as the serialized
 * search string does not change — safe to pass into `useEffect` / `useMemo`
 * dependency lists.
 */
export const useUrlSearchParams = (): URLSearchParams => {
  const searchStr = useLocation({ select: (l) => l.searchStr });
  return useMemo(() => new URLSearchParams(searchStr), [searchStr]);
};

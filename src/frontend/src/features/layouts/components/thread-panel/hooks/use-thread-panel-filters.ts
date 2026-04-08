import { useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { useSafeRouterPush } from "@/hooks/use-safe-router-push";

export const THREAD_PANEL_FILTER_PARAMS = [
  "has_unread",
  "has_starred",
  "has_mention",
] as const;

export type FilterType = (typeof THREAD_PANEL_FILTER_PARAMS)[number];

export const DEFAULT_SELECTED_FILTERS: FilterType[] = ["has_unread"];

export const useThreadPanelFilters = () => {
  const searchParams = useSearchParams();
  const safePush = useSafeRouterPush();

  const activeFilters = THREAD_PANEL_FILTER_PARAMS.reduce(
    (acc, param) => {
      acc[param] = searchParams.get(param) === "1";
      return acc;
    },
    {} as Record<FilterType, boolean>,
  );

  const hasActiveFilters = Object.values(activeFilters).some(Boolean);

  const applyFilters = useCallback(
    (filters: FilterType[]) => {
      const params = new URLSearchParams(searchParams.toString());
      THREAD_PANEL_FILTER_PARAMS.forEach((param) => params.delete(param));
      filters.forEach((filter) => params.set(filter, "1"));
      safePush(params);
    },
    [searchParams, safePush],
  );

  const clearFilters = useCallback(() => {
    const params = new URLSearchParams(searchParams.toString());
    THREAD_PANEL_FILTER_PARAMS.forEach((param) => params.delete(param));
    safePush(params);
  }, [searchParams, safePush]);

  return { hasActiveFilters, activeFilters, applyFilters, clearFilters };
};

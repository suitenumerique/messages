import { useSearchParams } from "next/navigation";
import {
  THREAD_PANEL_FILTER_PARAMS,
  type FilterType,
} from "../components/thread-panel-filter";

export const useThreadPanelFilters = () => {
  const searchParams = useSearchParams();

  const activeFilters = THREAD_PANEL_FILTER_PARAMS.reduce(
    (acc, param) => {
      acc[param] = searchParams.get(param) === "1";
      return acc;
    },
    {} as Record<FilterType, boolean>,
  );

  const hasActiveFilters = Object.values(activeFilters).some(Boolean);

  return { hasActiveFilters, activeFilters };
};

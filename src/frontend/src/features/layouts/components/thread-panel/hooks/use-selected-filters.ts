import { useSyncExternalStore } from "react";
import { THREAD_SELECTED_FILTERS_KEY } from "@/features/config/constants";
import {
  DEFAULT_SELECTED_FILTERS,
  THREAD_PANEL_FILTER_PARAMS,
  type FilterType,
} from "./use-thread-panel-filters";

/**
 * Which filters the quick-filter button applies, kept outside React on
 * purpose. The context menu freezes the items it was opened with, so a toggle
 * runs from a callback that may outlive the component that created it: reading
 * and writing the selection here makes every toggle start from the current
 * value instead of the one captured when the menu opened.
 */
const listeners = new Set<() => void>();
let selectedFilters: FilterType[] | null = null;

const readStoredFilters = (): FilterType[] => {
  try {
    const stored = JSON.parse(
      localStorage.getItem(THREAD_SELECTED_FILTERS_KEY) ?? "[]",
    );
    if (Array.isArray(stored)) {
      const validFilters = stored.filter(
        (value): value is FilterType =>
          typeof value === "string" &&
          THREAD_PANEL_FILTER_PARAMS.includes(value as FilterType),
      );
      if (validFilters.length > 0) {
        return validFilters;
      }
    }
  } catch {
    // ignore
  }
  return DEFAULT_SELECTED_FILTERS;
};

/**
 * The current selection. The value is cached so `useSyncExternalStore` gets a
 * stable reference between renders.
 */
export const getSelectedFilters = (): FilterType[] =>
  (selectedFilters ??= readStoredFilters());

export const setSelectedFilters = (filters: FilterType[]) => {
  selectedFilters = filters;
  try {
    localStorage.setItem(THREAD_SELECTED_FILTERS_KEY, JSON.stringify(filters));
  } catch {
    // ignore
  }
  listeners.forEach((listener) => listener());
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export const useSelectedFilters = (): FilterType[] =>
  useSyncExternalStore(subscribe, getSelectedFilters, getSelectedFilters);

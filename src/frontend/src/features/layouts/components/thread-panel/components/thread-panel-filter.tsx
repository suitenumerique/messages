import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { ContextMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { THREAD_SELECTED_FILTERS_KEY } from "@/features/config/constants";
import { useMailboxContext } from "@/features/providers/mailbox";
import {
  DEFAULT_SELECTED_FILTERS,
  THREAD_PANEL_FILTER_PARAMS,
  useThreadPanelFilters,
  type FilterType,
} from "../hooks/use-thread-panel-filters";

const getStoredSelectedFilters = (): FilterType[] => {
  try {
    const stored = JSON.parse(
      localStorage.getItem(THREAD_SELECTED_FILTERS_KEY) ?? "[]",
    );
    if (Array.isArray(stored) && stored.length > 0) {
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

export const ThreadPanelFilter = () => {
  const { t } = useTranslation();
  const [selectedFilters, setSelectedFilters] =
    useState<FilterType[]>(getStoredSelectedFilters);

  const { threads } = useMailboxContext();
  const { hasActiveFilters, activeFilters, applyFilters, clearFilters } =
    useThreadPanelFilters();
  const isDisabled = !threads?.results.length && !hasActiveFilters;

  const filterLabels: Record<FilterType, string> = useMemo(
    () => ({
      has_unread: t("Unread"),
      has_starred: t("Starred"),
      has_mention: t("Mentioned"),
    }),
    [t],
  );

  const handleToggleClick = () => {
    if (hasActiveFilters) {
      clearFilters();
    } else {
      applyFilters(selectedFilters);
    }
  };

  const handleSelectFilter = (type: FilterType) => {
    const toggled = selectedFilters.includes(type)
      ? selectedFilters.filter((f) => f !== type)
      : [...selectedFilters, type];
    const next = toggled.length > 0 ? toggled : DEFAULT_SELECTED_FILTERS;
    setSelectedFilters(next);
    localStorage.setItem(THREAD_SELECTED_FILTERS_KEY, JSON.stringify(next));
    if (hasActiveFilters) {
      applyFilters(next);
    }
  };

  const getTooltipContent = () => {
    if (hasActiveFilters) {
      const active = THREAD_PANEL_FILTER_PARAMS.filter(
        (param) => activeFilters[param],
      );
      return t("Active filters: {{filters}}", {
        filters: active.map((f) => filterLabels[f]).join(", "),
      });
    }
    return t("Filter by: {{filters}}", {
      filters: selectedFilters.map((f) => filterLabels[f]).join(", "),
    });
  };

  return (
    <ContextMenu
      options={THREAD_PANEL_FILTER_PARAMS.map((type) => ({
        label: filterLabels[type],
        icon: (
          <Icon
            name={selectedFilters.includes(type) ? "check_box" : "check_box_outline_blank"}
            type={IconType.OUTLINED}
          />
        ),
        callback: () => handleSelectFilter(type),
      }))}
    >
      <Tooltip content={getTooltipContent()} className={isDisabled ? "hidden" : ""}>
        <Button
          onClick={handleToggleClick}
          disabled={isDisabled}
          icon={<Icon name="filter_list" type={IconType.OUTLINED} />}
          variant={hasActiveFilters ? "secondary" : "tertiary"}
          size="medium"
          aria-label={t("Filter threads")}
        />
      </Tooltip>
    </ContextMenu>
  );
};

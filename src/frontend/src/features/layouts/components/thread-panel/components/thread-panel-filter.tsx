import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { ContextMenu, useContextMenuContext } from "@gouvfr-lasuite/ui-kit";
import type { MenuItem } from "@gouvfr-lasuite/ui-kit";
import { useMailboxContext } from "@/features/providers/mailbox";
import { isNativePlatform } from "@/features/native/platform";
import { useLongPress, type LongPressPosition } from "@/hooks/use-long-press";
import {
  DEFAULT_SELECTED_FILTERS,
  THREAD_PANEL_FILTER_PARAMS,
  useThreadPanelFilters,
  type FilterType,
} from "../hooks/use-thread-panel-filters";
import {
  getSelectedFilters,
  setSelectedFilters,
  useSelectedFilters,
} from "../hooks/use-selected-filters";
import { Icon } from "@/features/ui/components/icon";
import { Filter } from "@gouvfr-lasuite/ui-kit/icons";

// Items captured in the menu snapshot hold frozen callbacks, and a remount
// (route change…) would leave them bound to an instance that no longer
// renders. Routing them through a module-level ref keeps every toggle handled
// by the mounted filter, hence against the current URL filters.
const selectFilterRef: { current: (type: FilterType) => void } = {
  current: () => {},
};

export const ThreadPanelFilter = () => {
  const { t } = useTranslation();
  const selectedFilters = useSelectedFilters();

  const { threads } = useMailboxContext();
  const { hasActiveFilters, activeFilters, applyFilters, clearFilters } =
    useThreadPanelFilters();
  const { open } = useContextMenuContext();
  const isNative = isNativePlatform();
  // A long press fires a click on release; this guard skips the quick-toggle
  // that would otherwise run right after the menu opens.
  const longPressFiredRef = useRef(false);
  // The menu provider snapshots its items when it opens. Since the menu now
  // survives a selection, each toggle has to push a refreshed snapshot back —
  // reopening at the very same position, which keeps the popover in place.
  const menuPositionRef = useRef<LongPressPosition>({ x: 0, y: 0 });
  const isDisabled = !threads?.results.length && !hasActiveFilters;

  const filterLabels: Record<FilterType, string> = useMemo(
    () => ({
      has_unread: t("Unread"),
      has_starred: t("Starred"),
      has_mention: t("Mentioned"),
      has_assigned_to_me: t("Assigned to me"),
    }),
    [t],
  );

  const buildMenuItems = (selection: FilterType[]): MenuItem[] =>
    THREAD_PANEL_FILTER_PARAMS.map((type) => ({
      label: filterLabels[type],
      icon: (
        <Icon
          name={selection.includes(type) ? "check_box" : "check_box_outline_blank"}
        />
      ),
      // Filters are picked several at a time: only an outside click closes
      // the menu.
      isChecked: selection.includes(type),
      keepOpen: true,
      callback: () => selectFilterRef.current(type),
    }));

  const filterMenuItems = buildMenuItems(selectedFilters);

  const { handlers: longPressHandlers } = useLongPress((position) => {
    longPressFiredRef.current = true;
    menuPositionRef.current = position;
    open({ position, items: filterMenuItems });
  });

  const handleToggleClick = () => {
    // Ignore the synthetic click that follows a long press: the menu just opened.
    if (longPressFiredRef.current) {
      longPressFiredRef.current = false;
      return;
    }
    if (hasActiveFilters) {
      clearFilters();
    } else {
      applyFilters(selectedFilters);
    }
  };

  const handleSelectFilter = (type: FilterType) => {
    // Read the store rather than the render value: several filters are picked
    // in a row without the menu closing, so the toggle has to start from the
    // selection left by the previous one.
    const current = getSelectedFilters();
    const toggled = current.includes(type)
      ? current.filter((f) => f !== type)
      : [...current, type];
    const next = toggled.length > 0 ? toggled : DEFAULT_SELECTED_FILTERS;
    setSelectedFilters(next);
    if (hasActiveFilters) {
      applyFilters(next);
    }
    open({ position: menuPositionRef.current, items: buildMenuItems(next) });
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

  useEffect(() => {
    selectFilterRef.current = handleSelectFilter;
  });

  const trigger = (
    <Tooltip
      placement="right"
      content={getTooltipContent()}
      className={isDisabled ? "hidden" : ""}
    >
      <Button
        onClick={handleToggleClick}
        disabled={isDisabled}
        icon={hasActiveFilters ? <Icon name="filter-notification" size={22} /> : <Icon icon={Filter} size={22} />}
        variant="tertiary"
        color={isNative ? "neutral" : "brand"}
        size="small"
        aria-label={t("Filter threads")}
      />
    </Tooltip>
  );

  // Touch devices have no right-click/double-tap to summon the context menu, so
  // on the native app a long press opens it imperatively. On desktop the menu
  // stays wired to the ContextMenu wrapper (right-click / keyboard).
  if (isNative) {
    return (
      <span className="thread-panel__filter-trigger" {...longPressHandlers}>
        {trigger}
      </span>
    );
  }

  return (
    <ContextMenu options={filterMenuItems}>
      {/* The wrapper opens the menu at the pointer, but keeps that position to
          itself; mirroring it here is what lets a toggle reopen in place. */}
      <span
        className="thread-panel__filter-trigger"
        onContextMenu={(e) => {
          menuPositionRef.current = { x: e.clientX, y: e.clientY };
        }}
      >
        {trigger}
      </span>
    </ContextMenu>
  );
};

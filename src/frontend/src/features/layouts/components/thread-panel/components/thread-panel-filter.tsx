import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { ContextMenu, useContextMenuContext } from "@gouvfr-lasuite/ui-kit";
import type { MenuItem, MenuItemAction } from "@gouvfr-lasuite/ui-kit";
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
import { Filter, Star } from "@gouvfr-lasuite/ui-kit/icons";

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
  // A route change remounts the filter, and the remounted instance no longer
  // owns the open menu — `updateItems` would be a no-op on it. Reopening at
  // the very same position is what takes the menu back, keeping the popover
  // in place while its items are refreshed.
  const menuPositionRef = useRef<LongPressPosition>({ x: 0, y: 0 });
  const isDisabled = !threads?.results.length && !hasActiveFilters;

  const filterProps: Record<FilterType, Pick<MenuItemAction, "label" | "icon">> = useMemo(
    () => ({
      has_unread: { label: t("Unread"), icon: <Icon name="mail-unread" size={16} /> },
      has_starred: { label: t("Starred"), icon: <Icon icon={Star} size={16} /> },
      has_mention: { label: t("Mentioned"), icon: <Icon name="at-sign" size={16} /> },
      has_assigned_to_me: { label: t("Assigned to me"), icon: <Icon name="assign" size={16} /> },
    }),
    [t],
  );

  const buildMenuItems = (selection: FilterType[]): MenuItem[] =>
    THREAD_PANEL_FILTER_PARAMS.map((type) => ({
      ...filterProps[type],
      // Filters are picked several at a time: only an outside click closes
      // the menu.
      isChecked: selection.includes(type),
      keepOpen: true,
      callback: () => selectFilterRef.current(type),
    }));

  const filterMenuItems = buildMenuItems(selectedFilters);

  const toggleFilters = () => {
    if (hasActiveFilters) {
      clearFilters();
    } else {
      applyFilters(selectedFilters);
    }
  };

  // The menu is a react-aria popover: its underlay is mounted under the finger
  // while the long press is still held, so the click that would have followed
  // never reaches the button. Deciding tap vs long press from the touch
  // sequence itself is what keeps a short tap toggling the filters.
  const { handlers: longPressHandlers, isTouchHandled } = useLongPress(
    (position) => {
      menuPositionRef.current = position;
      open({ position, items: filterMenuItems });
    },
    { onTap: toggleFilters },
  );

  const handleToggleClick = () => {
    // Touch already ran the toggle from `onTap`; this is only the
    // compatibility click a browser emitted anyway.
    if (isTouchHandled()) return;
    toggleFilters();
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
    // The menu outlives a selection, so it has to be handed the refreshed
    // items or its checkboxes would keep showing the state it opened with.
    open({ position: menuPositionRef.current, items: buildMenuItems(next) });
  };

  const getTooltipContent = () => {
    if (hasActiveFilters) {
      const active = THREAD_PANEL_FILTER_PARAMS.filter(
        (param) => activeFilters[param],
      );
      return t("Active filters: {{filters}}", {
        filters: active.map((f) => filterProps[f].label).join(", "),
      });
    }
    return t("Filter by: {{filters}}", {
      filters: selectedFilters.map((f) => filterProps[f].label).join(", "),
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

import clsx from "clsx";
import { useTranslation } from "react-i18next";

/**
 * The two filter states for the notifications page.
 * "unread" shows only pending (non-traited) notifications.
 * "all" shows the full history.
 */
export type NotificationFilter = "unread" | "all";

type NotificationFilterTabsProps = {
  activeFilter: NotificationFilter;
  onFilterChange: (filter: NotificationFilter) => void;
};

/**
 * Tab bar for switching between "Unread" and "All" notification filters.
 * Per D-06 of the UI spec.
 */
export const NotificationFilterTabs = ({
  activeFilter,
  onFilterChange,
}: NotificationFilterTabsProps) => {
  const { t } = useTranslation();

  return (
    <div className="notification-filter-tabs" role="tablist">
      <button
        role="tab"
        type="button"
        aria-selected={activeFilter === "unread"}
        className={clsx("notification-filter-tabs__tab", {
          "notification-filter-tabs__tab--active": activeFilter === "unread",
        })}
        onClick={() => onFilterChange("unread")}
      >
        {t("Unread notifications")}
      </button>
      <button
        role="tab"
        type="button"
        aria-selected={activeFilter === "all"}
        className={clsx("notification-filter-tabs__tab", {
          "notification-filter-tabs__tab--active": activeFilter === "all",
        })}
        onClick={() => onFilterChange("all")}
      >
        {t("All notifications")}
      </button>
    </div>
  );
};

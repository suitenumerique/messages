import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useNotificationsList } from "@/features/api/gen/notifications/notifications";
import { TextLoader } from "@/features/ui/components/text-loader";
import { NotificationItem } from "../notification-item";
import type { EnrichedUserNotification } from "../../types";
import type { NotificationFilter } from "../notification-filter-tabs";

type NotificationListProps = {
  filter: NotificationFilter;
  onNotificationClick: (notification: EnrichedUserNotification) => void;
};

/**
 * Paginated list of notifications with empty state, error state, and
 * a "Load more" button when additional pages are available.
 *
 * Uses the generated useNotificationsList hook for data fetching.
 * The filter prop controls whether to show only unread (is_done=false)
 * or all notifications (no is_done filter).
 */
export const NotificationList = ({
  filter,
  onNotificationClick,
}: NotificationListProps) => {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  // Accumulate results across pages for the current filter
  const [accumulatedItems, setAccumulatedItems] = useState<
    EnrichedUserNotification[]
  >([]);

  // Reset pagination and accumulated items when filter changes
  useEffect(() => {
    setPage(1);
    setAccumulatedItems([]);
  }, [filter]);

  const queryParams =
    filter === "unread"
      ? { is_done: false, page }
      : { page };

  const { data, isLoading, isError } = useNotificationsList(queryParams);

  useEffect(() => {
    if (!data?.data?.results) return;

    // Cast needed: generated type has thread/thread_event as string, but API
    // returns nested objects. EnrichedUserNotification reflects the real shape.
    // TODO: remove cast once openapi.json is regenerated via `make api-update`.
    const newItems = data.data.results as unknown as EnrichedUserNotification[];

    if (page === 1) {
      setAccumulatedItems(newItems);
    } else {
      setAccumulatedItems((prev) => [...prev, ...newItems]);
    }
  }, [data, page]);

  const hasNextPage = Boolean(data?.data?.next);

  const handleLoadMore = () => {
    setPage((prev) => prev + 1);
  };

  if (isLoading && page === 1) {
    return (
      <div className="notification-list__loading">
        <TextLoader lines={5} />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="notification-list__error">
        <p className="notification-list__error-heading">
          {t("Could not load notifications")}
        </p>
        <p className="notification-list__error-body">
          {t("Check your connection and try again.")}
        </p>
      </div>
    );
  }

  if (!isLoading && accumulatedItems.length === 0) {
    if (filter === "unread") {
      return (
        <div className="notification-list__empty">
          <p className="notification-list__empty-heading">
            {t("You're all caught up!")}
          </p>
          <p className="notification-list__empty-body">
            {t("No pending mentions.")}
          </p>
        </div>
      );
    }
    return (
      <div className="notification-list__empty">
        <p className="notification-list__empty-heading">
          {t("No notifications.")}
        </p>
      </div>
    );
  }

  return (
    <div className="notification-list">
      <ul className="notification-list__items">
        {accumulatedItems.map((notification) => (
          <li key={notification.id}>
            <NotificationItem
              notification={notification}
              onClick={onNotificationClick}
            />
          </li>
        ))}
      </ul>

      {hasNextPage && (
        <div className="notification-list__load-more">
          <Button
            color="neutral"
            size="small"
            onClick={handleLoadMore}
            disabled={isLoading}
          >
            {t("Load more")}
          </Button>
        </div>
      )}
    </div>
  );
};

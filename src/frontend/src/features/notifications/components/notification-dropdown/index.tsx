import { useEffect, useRef, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { useQueryClient } from "@tanstack/react-query";
import {
  useNotificationsList,
  useNotificationsPartialUpdate,
  getNotificationsListQueryKey,
} from "@/features/api/gen/notifications/notifications";
import {
  getMailboxesListQueryKey,
} from "@/features/api/gen/mailboxes/mailboxes";
import type { mailboxesListResponse } from "@/features/api/gen/mailboxes/mailboxes";
import { useMailboxContext } from "@/features/providers/mailbox";
import type { EnrichedUserNotification } from "@/features/notifications/types";
import { NotificationItem } from "../notification-item";
import { NOTIFICATION_COUNT_QUERY_KEY } from "@/features/notifications/api";

type NotificationDropdownProps = {
  isOpen: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLDivElement | null>;
};

/**
 * Floating panel shown below the bell icon.
 *
 * Fetches the 3 most recent unread notifications when opened. Each
 * notification can be clicked to mark it as done and navigate to the
 * corresponding thread. A "See all" link navigates to /notifications.
 *
 * Per D-02, D-03 from the UI spec.
 */
export const NotificationDropdown = ({
  isOpen,
  onClose,
  anchorRef,
}: NotificationDropdownProps) => {
  const { t } = useTranslation();
  const router = useRouter();
  const queryClient = useQueryClient();
  const dropdownRef = useRef<HTMLDivElement>(null);
  const { selectedMailbox } = useMailboxContext();

  // Fetch first page of unread notifications; slice to 3 in render
  const { data: listData } = useNotificationsList(
    { is_done: false },
    {
      query: {
        enabled: isOpen,
      },
    },
  );

  const partialUpdate = useNotificationsPartialUpdate();

  // Close on click-outside (outside both dropdown and anchor)
  useEffect(() => {
    if (!isOpen) return;

    const handleMouseDown = (event: MouseEvent) => {
      const target = event.target as Node;
      const isInsideDropdown =
        dropdownRef.current?.contains(target) ?? false;
      const isInsideAnchor = anchorRef.current?.contains(target) ?? false;
      if (!isInsideDropdown && !isInsideAnchor) {
        onClose();
      }
    };

    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, [isOpen, onClose, anchorRef]);

  const resolveMailboxId = (): string | null => {
    if (selectedMailbox?.id) return selectedMailbox.id;

    // Fallback: read first mailbox from React Query cache
    const cached = queryClient.getQueryData<mailboxesListResponse>(
      getMailboxesListQueryKey(),
    );
    const firstMailbox = cached?.data?.[0];
    if (firstMailbox?.id) return firstMailbox.id;

    return null;
  };

  const handleNotificationClick = (notification: EnrichedUserNotification) => {
    if (!notification.thread?.id) {
      console.warn("Missing thread id, cannot navigate");
      onClose();
      return;
    }

    const mailboxId = resolveMailboxId();
    if (!mailboxId) {
      console.warn("No mailbox available, cannot navigate to thread");
      onClose();
      return;
    }

    // Optimistic update: mark as done in list cache
    queryClient.setQueryData(
      getNotificationsListQueryKey({ is_done: false }),
      (old: typeof listData) => {
        if (!old) return old;
        return {
          ...old,
          data: {
            ...old.data,
            results: old.data.results.map((n) =>
              n.id === notification.id ? { ...n, is_done: true } : n,
            ),
          },
        };
      },
    );

    // Invalidate count after mutation resolves
    partialUpdate.mutate(
      { id: notification.id, data: { is_done: true } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({
            queryKey: NOTIFICATION_COUNT_QUERY_KEY,
          });
          queryClient.invalidateQueries({
            queryKey: ["/api/v1.0/notifications/"],
          });
        },
      },
    );

    router.push(
      `/mailbox/${mailboxId}/thread/${notification.thread.id}`,
    );
    onClose();
  };

  const handleSeeAll = () => {
    router.push("/notifications");
    onClose();
  };

  // The generated hook returns { data: PaginatedUserNotificationList, status, headers }
  // Cast to EnrichedUserNotification[] since openapi.json lags behind nested serializers.
  // Slice to 3 to show only the most recent notifications in the dropdown.
  const notifications = (listData?.data?.results ??
    []).slice(0, 3) as unknown as EnrichedUserNotification[];

  if (!isOpen) return null;

  return (
    <div className="notification-dropdown" ref={dropdownRef} role="dialog" aria-label={t("Notifications")}>
      {notifications.length === 0 ? (
        <div className="notification-dropdown__empty">
          {t("You're all caught up!")}
        </div>
      ) : (
        notifications.map((notification) => (
          <NotificationItem
            key={notification.id}
            notification={notification}
            onClick={handleNotificationClick}
            compact
          />
        ))
      )}

      <div className="notification-dropdown__footer">
        <button
          type="button"
          className="notification-dropdown__see-all"
          onClick={handleSeeAll}
        >
          {t("See all")}
        </button>
      </div>
    </div>
  );
};

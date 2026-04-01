import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { MainLayout } from "@/features/layouts/components/main";
import { useMailboxContext } from "@/features/providers/mailbox";
import { NotificationList } from "@/features/notifications/components/notification-list";
import {
  NotificationFilterTabs,
  NotificationFilter,
} from "@/features/notifications/components/notification-filter-tabs";
import {
  useNotificationsPartialUpdate,
  getNotificationsListQueryKey,
} from "@/features/api/gen/notifications/notifications";
import {
  getMailboxesListQueryKey,
  type mailboxesListResponse,
} from "@/features/api/gen/mailboxes/mailboxes";
import {
  useMarkAllNotificationsDone,
  NOTIFICATION_COUNT_QUERY_KEY,
} from "@/features/notifications/api";
import type { EnrichedUserNotification } from "@/features/notifications/types";

/**
 * Notifications page — dedicated view for browsing and managing user notifications.
 *
 * Provides:
 * - A heading and a "Mark all as done" button (per D-07)
 * - Filter tabs for Unread/All (per D-06)
 * - A paginated notification list (per D-08)
 * - Navigation to the corresponding thread on click (per R8)
 */
const NotificationsPage = () => {
  const { t } = useTranslation();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { selectedMailbox } = useMailboxContext();
  const [filter, setFilter] = useState<NotificationFilter>("unread");

  const partialUpdateMutation = useNotificationsPartialUpdate();
  const markAllDoneMutation = useMarkAllNotificationsDone();

  /**
   * Resolve the mailbox id for navigation.
   * Prefers the currently selected mailbox from context.
   * Falls back to the first mailbox from the React Query cache.
   */
  const resolveMailboxId = useCallback((): string | null => {
    if (selectedMailbox?.id) {
      return selectedMailbox.id;
    }

    const cachedMailboxes = queryClient.getQueryData<mailboxesListResponse>(
      getMailboxesListQueryKey(),
    );
    const firstMailbox = cachedMailboxes?.data?.[0];
    if (firstMailbox?.id) {
      return firstMailbox.id;
    }

    return null;
  }, [selectedMailbox, queryClient]);

  const handleNotificationClick = useCallback(
    (notification: EnrichedUserNotification) => {
      if (!notification.thread?.id) {
        console.warn(
          "[NotificationsPage] Missing thread id, cannot navigate to thread",
        );
        return;
      }

      const mailboxId = resolveMailboxId();
      if (!mailboxId) {
        console.warn(
          "[NotificationsPage] No mailbox available, cannot navigate to thread",
        );
        return;
      }

      // Mark notification as done if not already
      if (!notification.is_done) {
        partialUpdateMutation.mutate(
          { id: notification.id, data: { is_done: true } },
          {
            onSuccess: () => {
              // Invalidate list and count after marking done
              queryClient.invalidateQueries({
                queryKey: getNotificationsListQueryKey(),
              });
              queryClient.invalidateQueries({
                queryKey: [...NOTIFICATION_COUNT_QUERY_KEY],
              });
            },
          },
        );
      }

      router.push(`/mailbox/${mailboxId}/thread/${notification.thread.id}`);
    },
    [resolveMailboxId, partialUpdateMutation, queryClient, router],
  );

  const handleMarkAllDone = useCallback(() => {
    markAllDoneMutation.mutate();
  }, [markAllDoneMutation]);

  return (
    <div style={{ maxWidth: 640, margin: "0 auto", padding: "48px 16px 0" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "var(--c--globals--spacings--md)",
        }}
      >
        <h1
          style={{
            fontSize: "var(--c--globals--font--sizes--lg)",
            fontWeight: "var(--c--globals--font--weights--bold)",
            margin: 0,
          }}
        >
          {t("Notifications")}
        </h1>

        {filter === "unread" && (
          <Button
            color="brand"
            size="small"
            onClick={handleMarkAllDone}
            disabled={markAllDoneMutation.isPending}
          >
            {t("Mark all as done")}
          </Button>
        )}
      </div>

      <NotificationFilterTabs
        activeFilter={filter}
        onFilterChange={setFilter}
      />

      <NotificationList
        filter={filter}
        onNotificationClick={handleNotificationClick}
      />
    </div>
  );
};

NotificationsPage.getLayout = (page: React.ReactElement) => {
  return <MainLayout>{page}</MainLayout>;
};

export default NotificationsPage;

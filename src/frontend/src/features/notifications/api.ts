/**
 * Notification API wrappers.
 *
 * @TODO: These manual wrappers are tech debt. Once Docker is available and
 * `make api-update` can be run, these should be replaced by auto-generated
 * Orval hooks from `src/features/api/gen/notifications/notifications.ts`.
 * The openapi.json needs to be regenerated to include the `count` and
 * `mark-all-done` custom actions from UserNotificationViewSet.
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type {
  UseQueryOptions,
  UseQueryResult,
  UseMutationResult,
} from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetch-api";

export type NotificationCount = {
  count: number;
};

export type NotificationMarkAllDone = {
  updated: number;
};

export const NOTIFICATION_COUNT_QUERY_KEY = [
  "/api/v1.0/notifications/count/",
] as const;

/**
 * Fetch the count of unread notifications for the current user.
 */
export const fetchNotificationsCount = async (): Promise<NotificationCount> => {
  const response = await fetchAPI<{ data: NotificationCount; status: number }>(
    "/api/v1.0/notifications/count/",
    { method: "GET" },
  );
  return response.data;
};

/**
 * React Query hook for unread notification count.
 */
export const useNotificationsCount = <
  TData = NotificationCount,
  TError = unknown,
>(
  options?: Partial<UseQueryOptions<NotificationCount, TError, TData>>,
): UseQueryResult<TData, TError> => {
  return useQuery<NotificationCount, TError, TData>({
    queryKey: NOTIFICATION_COUNT_QUERY_KEY,
    queryFn: fetchNotificationsCount,
    ...options,
  });
};

/**
 * Mark all notifications as done for the current user.
 */
export const markAllNotificationsDone =
  async (): Promise<NotificationMarkAllDone> => {
    const response = await fetchAPI<{
      data: NotificationMarkAllDone;
      status: number;
    }>("/api/v1.0/notifications/mark-all-done/", { method: "POST" });
    return response.data;
  };

/**
 * React Query mutation hook for marking all notifications as done.
 * Automatically invalidates the notification list and count queries on success.
 */
export const useMarkAllNotificationsDone = (): UseMutationResult<
  NotificationMarkAllDone,
  unknown,
  void,
  unknown
> => {
  const queryClient = useQueryClient();

  return useMutation<NotificationMarkAllDone, unknown, void>({
    mutationFn: markAllNotificationsDone,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["/api/v1.0/notifications/"],
      });
      queryClient.invalidateQueries({
        queryKey: NOTIFICATION_COUNT_QUERY_KEY,
      });
    },
  });
};

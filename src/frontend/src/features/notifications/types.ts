/**
 * Shared types for the notification center feature.
 *
 * The UserNotification data field is a JSONField on the backend with
 * default=dict. For mention notifications it may carry extra metadata, but
 * sender and thread information should be read from the nested thread_event and
 * thread objects returned by the serializer.
 *
 * Note: The generated UserNotification type in api/gen/models still has
 * thread: string | null and thread_event: string | null (stale openapi.json).
 * The EnrichedUserNotification type below reflects the actual API response
 * with nested serializers.
 * TODO: Remove EnrichedUserNotification once openapi.json is regenerated via
 * `make api-update` and Orval types are updated.
 */

/**
 * Author information as returned by UserWithoutAbilitiesSerializer.
 */
export type NotificationAuthor = {
  id: string;
  email: string;
  full_name: string | null;
  custom_attributes: Record<string, unknown>;
};

/**
 * Nested thread info as returned by NotificationThreadSerializer.
 */
export type NotificationThread = {
  id: string;
  subject: string;
};

/**
 * Nested thread event info as returned by NotificationThreadEventSerializer.
 */
export type NotificationThreadEvent = {
  id: string;
  author: NotificationAuthor;
  content: string;
};

/**
 * UserNotification as actually returned by the API with nested serializers.
 * Replaces the stale generated type until openapi.json is regenerated.
 */
export type EnrichedUserNotification = {
  readonly id: string;
  readonly user: string;
  readonly type: string;
  is_done?: boolean;
  readonly data: unknown;
  readonly thread: NotificationThread | null;
  readonly thread_event: NotificationThreadEvent | null;
  readonly created_at: string;
  readonly updated_at: string;
};

/**
 * Shape of the data field for mention-type notifications.
 * Currently the backend creates mentions with data={} (empty dict), so this
 * type guard future-proofs the code for when richer metadata is stored there.
 */
export type NotificationMentionData = {
  sender_name: string;
  thread_title: string;
};

/**
 * Type guard to safely narrow the unknown data field of a UserNotification
 * to the NotificationMentionData shape.
 */
export const isMentionData = (
  data: unknown,
): data is NotificationMentionData =>
  typeof data === "object" &&
  data !== null &&
  typeof (data as Record<string, unknown>).sender_name === "string" &&
  typeof (data as Record<string, unknown>).thread_title === "string";

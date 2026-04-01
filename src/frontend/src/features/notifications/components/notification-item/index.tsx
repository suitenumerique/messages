import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { DateHelper } from "@/features/utils/date-helper";
import { isMentionData } from "../../types";
import type { EnrichedUserNotification } from "../../types";

/**
 * Color palette for avatar backgrounds.
 * Colors are muted brand tones derived from the Cunningham design system palette.
 */
const AVATAR_COLORS = [
  "#3a6ea8",
  "#7b5ea7",
  "#2e8b7a",
  "#c0622c",
  "#5a7d3c",
  "#8b3a5e",
];

/**
 * Derive a stable background color for an avatar from the author name.
 * Uses a simple polynomial hash so the same name always gets the same color.
 */
const getAvatarColor = (name: string): string => {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) & 0xffffffff;
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
};

/**
 * Extract initials (up to 2 characters) from a display name.
 * Uses first letter of first and last word.
 */
const getInitials = (name: string): string => {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) {
    return parts[0].charAt(0).toUpperCase();
  }
  return (
    parts[0].charAt(0).toUpperCase() +
    parts[parts.length - 1].charAt(0).toUpperCase()
  );
};

type NotificationItemProps = {
  notification: EnrichedUserNotification;
  onClick?: (notification: EnrichedUserNotification) => void;
  /** true in dropdown (compact padding), false in page list (default) */
  compact?: boolean;
};

/**
 * Single notification row component.
 *
 * Displays an unread indicator dot, sender avatar with initials (per D-04),
 * notification text (sender mentioned you in thread), and a relative date.
 * Visual state differs between unread (bold text, blue dot) and treated
 * (grey text, no dot).
 */
export const NotificationItem = ({
  notification,
  onClick,
  compact = false,
}: NotificationItemProps) => {
  const { t, i18n } = useTranslation();

  // Resolve sender name: prefer nested thread_event author, fall back to data field
  const author = notification.thread_event?.author;
  const senderName =
    author?.full_name ??
    author?.email?.split("@")[0] ??
    (isMentionData(notification.data) ? notification.data.sender_name : null) ??
    t("Someone");

  // Resolve thread subject: prefer nested thread object, fall back to data field
  const threadSubject =
    notification.thread?.subject ??
    (isMentionData(notification.data) ? notification.data.thread_title : null) ??
    t("a thread");

  const relativeDate = DateHelper.formatRelativeTime(
    notification.created_at,
    new Date(),
  );

  const formattedDate =
    relativeDate ||
    DateHelper.formatDate(notification.created_at, i18n.language, false);

  const initials = getInitials(senderName);
  const avatarBg = getAvatarColor(senderName);
  const isUnread = !notification.is_done;

  const handleClick = () => {
    onClick?.(notification);
  };

  return (
    <button
      type="button"
      className={clsx("notification-item", {
        "notification-item--compact": compact,
      })}
      onClick={handleClick}
      aria-label={t("Notification: {{sender}} mentioned you in {{thread}}", {
        sender: senderName,
        thread: threadSubject,
      })}
    >
      {/* Unread dot or spacer */}
      <span
        className={clsx("notification-item__dot", {
          "notification-item__dot--active": isUnread,
        })}
        aria-hidden="true"
      />

      {/* Avatar with sender initials (per D-04) */}
      <div
        className="notification-item__avatar"
        style={{ backgroundColor: avatarBg }}
        aria-hidden="true"
      >
        {initials}
      </div>

      {/* Text content */}
      <div className="notification-item__content">
        <span
          className={clsx("notification-item__text", {
            "notification-item__text--unread": isUnread,
            "notification-item__text--treated": !isUnread,
          })}
        >
          {t("{{sender}} mentioned you in {{thread}}", {
            sender: senderName,
            thread: threadSubject,
          })}
        </span>
      </div>

      {/* Relative date */}
      <time
        className="notification-item__date"
        dateTime={notification.created_at}
        title={notification.created_at}
      >
        {formattedDate}
      </time>
    </button>
  );
};

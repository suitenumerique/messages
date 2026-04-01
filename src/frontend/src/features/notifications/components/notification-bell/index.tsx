import { useRef, useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { Badge } from "@/features/ui/components/badge";
import { useNotificationsCount } from "@/features/notifications/api";
import { NotificationDropdown } from "../notification-dropdown";

/**
 * Bell icon with unread count badge, shown in the application header.
 *
 * Polls the notifications count endpoint every 30s. Shows a badge with the
 * number of unread notifications (capped at "99+" when >= 100). Clicking the
 * bell opens the NotificationDropdown floating panel.
 *
 * Per D-01, D-02, D-09 from the UI spec.
 */
export const NotificationBell = () => {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [isPulsing, setIsPulsing] = useState(false);
  const prevCountRef = useRef<number | null>(null);

  const { data: countData } = useNotificationsCount({
    refetchInterval: 30 * 1000,
    staleTime: 30 * 1000,
  });

  const count = countData?.count ?? 0;
  const badgeLabel = count >= 100 ? "99+" : String(count);

  // Trigger pulse animation when count increases
  useEffect(() => {
    const prev = prevCountRef.current;
    if (prev !== null && count > prev) {
      setIsPulsing(true);
      const timer = setTimeout(() => setIsPulsing(false), 300);
      return () => clearTimeout(timer);
    }
    prevCountRef.current = count;
  }, [count]);

  return (
    <div className="notification-bell" ref={containerRef}>
      <Tooltip content={t("Notifications")}>
        <Button
          size="medium"
          color="brand"
          variant="tertiary"
          icon={<Icon name="notifications" />}
          aria-label={t("Notifications")}
          onClick={() => setIsOpen((prev) => !prev)}
        />
      </Tooltip>

      {count > 0 && (
        <span
          className={
            isPulsing
              ? "notification-bell__badge notification-bell__badge--pulse"
              : "notification-bell__badge"
          }
          aria-hidden="true"
        >
          <Badge color="brand" variant="primary" compact>
            {badgeLabel}
          </Badge>
        </span>
      )}

      {isOpen && (
        <NotificationDropdown
          isOpen={isOpen}
          onClose={() => setIsOpen(false)}
          anchorRef={containerRef}
        />
      )}
    </div>
  );
};

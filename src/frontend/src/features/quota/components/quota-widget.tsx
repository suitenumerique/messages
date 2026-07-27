import { StorageGauge } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { StorageEntitlement } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useModalStore } from "@/features/providers/modal-store";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { useMailboxEntitlements } from "../api/use-mailbox-entitlements";
import "./quota-widget.scss";

const BYTES_PER_GB = 1000 ** 3;

type QuotaWidgetProps = {
  mailboxId: string | undefined;
};

/**
 * Storage gauge shown at the bottom of the sidebar.
 *
 * Renders one gauge for the mailbox ("account") and, when the mailbox belongs
 * to an organization, a second gauge for the organization aggregate. A level
 * whose ``max_storage`` is null has no known limit and is not rendered.
 *
 * Mailbox admins can click a gauge to open the Storage settings tab (the
 * detailed breakdown lives there); for other members the gauge is informational
 * only, since that tab is admin-gated.
 */
export const QuotaWidget = ({ mailboxId }: QuotaWidgetProps) => {
  const { t } = useTranslation();
  const { selectedMailbox } = useMailboxContext();
  const { openModal } = useModalStore();
  const { data } = useMailboxEntitlements(mailboxId);

  const entitlements = data?.data;
  if (!entitlements) {
    return null;
  }

  const canOpenStorageTab = selectedMailbox?.abilities.manage_accesses ?? false;
  const onOpen = canOpenStorageTab
    ? () => openModal(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "storage" })
    : undefined;

  const unit = t("GB");
  const account = renderGauge(entitlements.account, unit, onOpen);
  const organization = entitlements.organization
    ? renderGauge(entitlements.organization, unit, onOpen)
    : null;

  if (!account && !organization) {
    return null;
  }

  return (
    <div className="quota-widget">
      {account && (
        <div className="quota-widget__level">
          <span className="quota-widget__caption">{t("Mailbox storage")}</span>
          {account}
        </div>
      )}
      {organization && (
        <div className="quota-widget__level">
          <span className="quota-widget__caption">
            {t("Organization storage")}
          </span>
          {organization}
        </div>
      )}
    </div>
  );
};

const renderGauge = (
  level: StorageEntitlement,
  unit: string,
  onClick?: () => void,
) => {
  // No gauge without a positive limit: null means "unknown" and 0 means
  // "unlimited". Either way there is nothing to gauge against, so the sidebar
  // widget is hidden (usage stays visible in the Storage settings tab).
  if (level.max_storage == null || level.max_storage <= 0) {
    return null;
  }
  return (
    <StorageGauge
      used={level.storage_used / BYTES_PER_GB}
      total={level.max_storage / BYTES_PER_GB}
      unit={unit}
      onClick={onClick}
      className={onClick ? "quota-widget__gauge--clickable" : undefined}
    />
  );
};

import { IconSize, StorageGaugeButton } from "@gouvfr-lasuite/ui-components";
import { Warning } from "@gouvfr-lasuite/ui-components/icons";
import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useModalStore } from "@/features/providers/modal-store";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { QuotaHelper } from "@/features/utils/quota-helper";
import { useMailboxEntitlements } from "../../api/use-mailbox-entitlements";

type QuotaWidgetProps = {
  mailboxId: string | undefined;
};

/**
 * Storage gauge shown at the bottom of the sidebar, a single gauge for the mailbox quota.
 * The organization never gets a gauge of its own, it only surfaces once its quota is
 * full by locking the gauge.
 * Nothing is rendered when there is no limit to gauge against.
 *
 * Mailbox admins can click the gauge to open the Storage settings tab; for
 * other members it is informational only, since that tab is admin-gated.
 */
export const QuotaWidget = ({ mailboxId }: QuotaWidgetProps) => {
  const { t } = useTranslation();
  const { selectedMailbox } = useMailboxContext();
  const { openModal } = useModalStore();
  const { data } = useMailboxEntitlements(mailboxId);

  const entitlements = data?.data;
  if (!entitlements) return null;

  const isOrganizationFull = QuotaHelper.isQuotaReached(entitlements.organization);
  const { storage_used, max_storage } = entitlements.account;
  if (!isOrganizationFull && !QuotaHelper.hasLimit(entitlements.account)) return null;

  const canOpenStorageTab = selectedMailbox?.abilities.manage_accesses ?? false;
  const onOpen = canOpenStorageTab
    ? () => openModal(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "storage" })
    : undefined;

  return (
    <div className="quota-widget">
      <StorageGaugeButton
        used={QuotaHelper.toGigabytes(storage_used)}
        total={QuotaHelper.toGigabytes(max_storage ?? 0)}
        unit={t("GB")}
        locked={isOrganizationFull}
        lockedContent={
          <span className="quota-widget__locked-content">
            <Warning size={IconSize.SMALL} aria-hidden />
            <span className="quota-widget__locked-label" title={t("Domain's storage exceeded")}>
              {t("Domain's storage exceeded")}
            </span>
          </span>
        }
        onClick={onOpen}
        className={clsx({ "quota-widget__gauge--clickable": onOpen })}
      />
    </div>
  );
};

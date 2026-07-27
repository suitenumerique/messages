import {
  IconSize,
  StorageGaugeBar,
  StorageGaugeButton,
  StorageGaugeProps,
  useStorageGauge,
} from "@gouvfr-lasuite/ui-components";
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
  const gaugeProps: StaticStorageGaugeProps = {
    used: QuotaHelper.toGigabytes(storage_used),
    total: QuotaHelper.toGigabytes(max_storage ?? 0),
    unit: t("GB"),
    locked: isOrganizationFull,
    lockedContent: (
      <span className="quota-widget__locked-content">
        <Warning size={IconSize.SMALL} aria-hidden />
        <span className="quota-widget__locked-label" title={t("Domain's storage exceeded")}>
          {t("Domain's storage exceeded")}
        </span>
      </span>
    ),
  };

  return (
    <div className="quota-widget">
      {canOpenStorageTab ? (
        <StorageGaugeButton
          {...gaugeProps}
          onClick={() => openModal(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "storage" })}
        />
      ) : (
        <StaticStorageGauge {...gaugeProps} />
      )}
    </div>
  );
};

type StaticStorageGaugeProps = Pick<StorageGaugeProps, "used" | "total" | "unit" | "locked" | "lockedContent">;

/**
 * Non-interactive twin of `StorageGaugeButton`: the library only ships the gauge
 * as a button, which would advertise an action (hover, focus, button role) to
 * members who cannot open the Storage tab. It mirrors the library markup so
 * both variants share the same styles.
 */
const StaticStorageGauge = ({ used, total, unit, locked, lockedContent }: StaticStorageGaugeProps) => {
  const { formattedUsed, formattedTotal } = useStorageGauge({ used, total });

  return (
    <div
      className={clsx("c__storage-gauge", "quota-widget__gauge--static", {
        "c__storage-gauge--locked": locked,
      })}
    >
      {locked ? (
        <div className="c__storage-gauge__locked">{lockedContent}</div>
      ) : (
        <>
          <StorageGaugeBar used={used} total={total} />
          <span className="c__storage-gauge__label">
            {formattedUsed}&nbsp;/&nbsp;{formattedTotal}&nbsp;{unit}
          </span>
        </>
      )}
    </div>
  );
};

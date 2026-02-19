import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useEntitlements } from "@/features/quota/api/use-entitlements";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export const QuotaWidget = () => {
  const { t } = useTranslation();
  const { selectedMailbox } = useMailboxContext();
  const { data, isError } = useEntitlements(selectedMailbox?.id);

  const mailboxData = data?.data?.mailbox;

  // Don't render if no mailbox is selected
  if (!selectedMailbox) return null;

  // Don't render if entitlements fetch failed
  if (isError) return null;

  // Don't render if no storage data (e.g. dummy backend)
  if (!mailboxData || mailboxData.max_storage === null) return null;

  const maxStorage = mailboxData.max_storage;
  const storageUsed = mailboxData.storage_used ?? 0;
  const percentage = maxStorage > 0 ? Math.min((storageUsed / maxStorage) * 100, 100) : 0;

  return (
    <div className="quota-widget">
      <div className="quota-widget__label">
        {t("Storage")}
      </div>
      <div className="quota-widget__bar">
        <div
          className="quota-widget__bar__fill"
          style={{ width: `${percentage}%` }}
          data-warning={percentage > 80 ? "" : undefined}
          data-critical={percentage > 95 ? "" : undefined}
        />
      </div>
      <div className="quota-widget__text">
        {t("{{used}} / {{total}} used", {
          used: formatBytes(storageUsed),
          total: formatBytes(maxStorage),
        })}
      </div>
    </div>
  );
};

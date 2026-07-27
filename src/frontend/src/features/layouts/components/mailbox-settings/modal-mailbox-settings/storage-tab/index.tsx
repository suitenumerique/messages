import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner, StorageGauge } from "@gouvfr-lasuite/ui-kit";
import { Link } from "@tanstack/react-router";
import clsx from "clsx";
import { MouseEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "@/features/i18n/initI18n";
import {
  Mailbox,
  StorageEntitlement,
  useMailboxesStorageRetrieve,
} from "@/features/api/gen";
import useTrash from "@/features/message/use-trash";
import { Banner } from "@/features/ui/components/banner";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DateHelper } from "@/features/utils/date-helper";
import { useMailboxEntitlements } from "@/features/quota/api/use-mailbox-entitlements";

const BYTES_PER_GB = 1000 ** 3;

type MailboxSettingsStorageTabProps = {
  mailbox: Mailbox;
  /** Closes the settings modal — called when the user follows a conversation
   * deep-link so they land on the thread instead of behind the modal. */
  onClose: () => void;
};

/**
 * Storage overview for a mailbox: the quota gauge (how much of the allowance is
 * used, at the mailbox and — when relevant — organization level), the total
 * space used with its trash/spam split, and the top-100 largest conversations
 * so an admin can see what is taking up room, jump to a conversation, or move it
 * to the trash. Reachable only by mailbox admins (the settings modal gates this
 * tab on `manage_accesses`).
 */
export const MailboxSettingsStorageTab = ({
  mailbox,
  onClose,
}: MailboxSettingsStorageTabProps) => {
  const { t } = useTranslation();
  const language = i18n.resolvedLanguage;

  // Never surface a bare "0 B"/"0o": zero storage reads as "Empty".
  const formatSize = (bytes: number) =>
    bytes === 0 ? t("Empty") : AttachmentHelper.getFormattedSize(bytes, language);

  const { data: entitlementsData } = useMailboxEntitlements(mailbox.id);
  const { data, isLoading, error } = useMailboxesStorageRetrieve(mailbox.id);

  // `useTrash` shows a toast with a built-in Undo and handles cache
  // invalidation. On success we just drop the row from the list; undoing from
  // the toast restores it server-side and it reappears on the next refresh.
  const { markAsTrashed } = useTrash();
  const [trashedIds, setTrashedIds] = useState<Set<string>>(new Set());

  const handleTrash = (threadId: string) => {
    markAsTrashed({
      threadIds: [threadId],
      mailboxId: mailbox.id,
      onSuccess: () => setTrashedIds((prev) => new Set(prev).add(threadId)),
    });
  };

  // Modifier/middle clicks open the conversation in a new tab — leave the modal
  // open in that case; only a plain left click navigates in place and closes it.
  const handleFollowLink = (event: MouseEvent) => {
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    ) {
      return;
    }
    onClose();
  };

  const entitlements = entitlementsData?.data;

  if (isLoading) {
    return (
      <div className="mailbox-settings__tab mailbox-settings__storage">
        <Banner type="info" icon={<Spinner />}>
          {t("Loading storage statistics...")}
        </Banner>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mailbox-settings__tab mailbox-settings__storage">
        <Banner type="error">
          {t("Error while loading storage statistics")}
        </Banner>
      </div>
    );
  }

  const stats = data.data;

  const unit = t("GB");
  // The account gauge already shows total-used-over-limit, so its caption *is*
  // the "total storage used" label — no separate headline needed.
  const accountGauge = entitlements
    ? renderGauge(t("Total storage used"), entitlements.account, unit)
    : null;
  const organizationGauge = entitlements?.organization
    ? renderGauge(t("Organization storage"), entitlements.organization, unit)
    : null;
  const gauges = [accountGauge, organizationGauge].filter(Boolean);

  return (
    <div className="mailbox-settings__tab mailbox-settings__storage">
      {gauges.length > 0 && (
        <section className="mailbox-settings__section">
          <div className="mailbox-settings__storage-gauges">{gauges}</div>
        </section>
      )}

      <section className="mailbox-settings__section">
        {/* Fallback when storage is unlimited (no account gauge): the gauge
            would otherwise be the only place the total appears. */}
        {!accountGauge && (
          <div className="mailbox-settings__storage-total">
            <span className="mailbox-settings__storage-total-value">
              {formatSize(stats.total_storage)}
            </span>
            <span className="mailbox-settings__storage-total-label">
              {t("Total storage used")}
            </span>
          </div>
        )}
        <div className="mailbox-settings__storage-summary">
          <div className="mailbox-settings__storage-metric">
            <span className="mailbox-settings__storage-metric-value">
              {stats.message_count.toLocaleString(language)}
            </span>
            <span className="mailbox-settings__storage-metric-label">
              {t("Messages")}
            </span>
          </div>
          <div className="mailbox-settings__storage-metric">
            <span className="mailbox-settings__storage-metric-value">
              {stats.thread_count.toLocaleString(language)}
            </span>
            <span className="mailbox-settings__storage-metric-label">
              {t("Conversations")}
            </span>
          </div>
          {/* Links to the Trash folder so an admin can jump there and empty it
              (the "Empty trash" action lives in the thread-list header). */}
          <Link
            to="/mailbox/$mailboxId"
            params={{ mailboxId: mailbox.id }}
            search={{ has_trashed: "1" }}
            className="mailbox-settings__storage-metric mailbox-settings__storage-metric--link"
            onClick={handleFollowLink}
          >
            <span className="mailbox-settings__storage-metric-value">
              {formatSize(stats.trashed_storage + stats.spam_storage)}
            </span>
            <span className="mailbox-settings__storage-metric-label">
              {t("Trash and spam")}
            </span>
          </Link>
        </div>
      </section>

      {stats.message_count === 0 ? (
        <div className="mailbox-settings__storage-empty">
          <p className="mailbox-settings__storage-empty-title">
            {t("This mailbox is empty")}
          </p>
          <p className="mailbox-settings__storage-empty-description">
            {t("Storage usage will appear here once this mailbox has messages.")}
          </p>
        </div>
      ) : (
        <section className="mailbox-settings__section">
          <header className="mailbox-settings__section-header">
            <h3 className="mailbox-settings__section-title">
              {t("Largest conversations")}
            </h3>
          </header>

          <ul className="mailbox-settings__storage-list">
            {stats.largest_threads
              .filter((thread) => !trashedIds.has(thread.id))
              .map((thread) => (
                <li
                  key={thread.id}
                  className={clsx("mailbox-settings__storage-item", {
                    "mailbox-settings__storage-item--unread": thread.is_unread,
                  })}
                >
                  <span
                    className="mailbox-settings__storage-item-status"
                    aria-label={thread.is_unread ? t("Unread") : t("Read")}
                  />
                  <Link
                    to="/mailbox/$mailboxId/thread/$threadId"
                    params={{ mailboxId: mailbox.id, threadId: thread.id }}
                    className="mailbox-settings__storage-item-link"
                    onClick={handleFollowLink}
                  >
                    <span className="mailbox-settings__storage-item-main">
                      <span className="mailbox-settings__storage-item-subject">
                        {thread.subject || t("No subject")}
                      </span>
                      <span className="mailbox-settings__storage-item-meta">
                        {t("{{count}} messages", { count: thread.message_count })}
                        {thread.messaged_at
                          ? ` · ${DateHelper.formatDate(thread.messaged_at, language, false)}`
                          : ""}
                      </span>
                    </span>
                    <span className="mailbox-settings__storage-item-size">
                      {formatSize(thread.size)}
                    </span>
                  </Link>
                  <Button
                    className="mailbox-settings__storage-item-trash"
                    size="nano"
                    variant="tertiary"
                    aria-label={t("Move to trash")}
                    icon={<Icon name="delete" type={IconType.OUTLINED} />}
                    onClick={() => handleTrash(thread.id)}
                  />
                </li>
              ))}
          </ul>
        </section>
      )}
    </div>
  );
};

const renderGauge = (
  caption: string,
  level: StorageEntitlement,
  unit: string,
) => {
  // No gauge without a positive limit (null = unknown, 0 = unlimited); the
  // usage summary below still conveys how much is stored.
  if (level.max_storage == null || level.max_storage <= 0) {
    return null;
  }
  return (
    <div key={caption} className="mailbox-settings__storage-gauge">
      <span className="mailbox-settings__storage-gauge-caption">{caption}</span>
      <StorageGauge
        used={level.storage_used / BYTES_PER_GB}
        total={level.max_storage / BYTES_PER_GB}
        unit={unit}
      />
    </div>
  );
};

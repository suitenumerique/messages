import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Link } from "@tanstack/react-router";
import clsx from "clsx";
import { MouseEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "@/features/i18n/initI18n";
import { Mailbox, useMailboxesStatsStorageRetrieve } from "@/features/api/gen";
import useTrash from "@/features/message/use-trash";
import { Banner } from "@/features/ui/components/banner";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DateHelper } from "@/features/utils/date-helper";

type MailboxSettingsStorageTabProps = {
  mailbox: Mailbox;
  /** Closes the settings modal — called when the user follows a conversation
   * deep-link so they land on the thread instead of behind the modal. */
  onClose: () => void;
};

/**
 * Read-only storage overview for a mailbox: the total space it uses (computed
 * server-side with the same formula as the metrics endpoints), plus the top-100
 * largest conversations so an admin can see what is taking up room, jump
 * straight to a conversation, or move it to the trash. Reachable only by mailbox
 * admins (the settings modal gates this tab on `manage_accesses`).
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

  const { data, isLoading, error } = useMailboxesStatsStorageRetrieve(
    mailbox.id,
  );

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

  // Nothing has ever been stored: show a clear empty state rather than a wall of
  // zeroes and an empty list.
  if (stats.message_count === 0) {
    return (
      <div className="mailbox-settings__tab mailbox-settings__storage">
        <div className="mailbox-settings__storage-empty">
          <p className="mailbox-settings__storage-empty-title">
            {t("This mailbox is empty")}
          </p>
          <p className="mailbox-settings__storage-empty-description">
            {t(
              "Storage usage will appear here once this mailbox has messages.",
            )}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mailbox-settings__tab mailbox-settings__storage">
      <section className="mailbox-settings__section">
        <div className="mailbox-settings__storage-summary">
          <div className="mailbox-settings__storage-metric">
            <span className="mailbox-settings__storage-metric-value">
              {formatSize(stats.total_storage)}
            </span>
            <span className="mailbox-settings__storage-metric-label">
              {t("Total storage used")}
            </span>
          </div>
          <div className="mailbox-settings__storage-metric">
            <span className="mailbox-settings__storage-metric-value">
              {formatSize(stats.trashed_storage + stats.spam_storage)}
            </span>
            <span className="mailbox-settings__storage-metric-label">
              {t("Trash and spam")}
            </span>
          </div>
          <div className="mailbox-settings__storage-metric">
            <span className="mailbox-settings__storage-metric-value">
              {stats.message_count.toLocaleString(language)}
            </span>
            <span className="mailbox-settings__storage-metric-label">
              {t("Messages")}
            </span>
          </div>
        </div>
      </section>

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
    </div>
  );
};

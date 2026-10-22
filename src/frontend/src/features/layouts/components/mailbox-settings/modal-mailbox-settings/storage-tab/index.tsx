import { Button, Checkbox, Icon, IconType, Spinner, StorageGaugeInformation } from "@gouvfr-lasuite/ui-components";
import { createLink, useRouter } from "@tanstack/react-router";
import ViewHelper from "@/features/utils/view-helper";
import clsx from "clsx";
import { MouseEvent, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import i18n from "@/features/i18n/initI18n";
import {
  getMailboxesStorageRetrieveQueryKey,
  LargestThread,
  Mailbox,
  mailboxesStorageRetrieveResponse,
  StorageEntitlement,
  useMailboxesStorageRetrieve,
} from "@/features/api/gen";
import useTrash from "@/features/message/use-trash";
import { isNativePlatform } from "@/features/native/platform";
import { Banner } from "@/features/ui/components/banner";
import { Tooltip } from "@/features/ui/components/tooltip";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DateHelper } from "@/features/utils/date-helper";
import { QuotaHelper } from "@/features/utils/quota-helper";
import { Abilities } from "@/hooks/use-ability";
import { useMailboxEntitlements } from "@/features/quota/api/use-mailbox-entitlements";

// A Cunningham button rendered as a router link: client-side navigation, while
// a modifier/middle click still opens a new tab.
const ButtonLink = createLink(Button);

type MailboxSettingsStorageTabProps = {
  mailbox: Mailbox;
  /** Called when the user follows a folder or conversation link in place, so
   * they land on it instead of behind the modal (and, on mobile, behind the
   * left panel the modal was opened from). */
  onNavigate: () => void;
};

/**
 * Storage overview for a mailbox: the quota gauge (how much of the mailbox
 * allowance is used, with a warning once the domain is full), the total
 * space used with its trash/spam split, and the top-100 largest conversations
 * so an admin can see what is taking up room, open a conversation (in a new tab
 * on the web), or move one or a selection of them to the trash. Reachable only
 * by mailbox admins (the settings modal gates this tab on `manage_accesses`).
 */
export const MailboxSettingsStorageTab = ({
  mailbox,
  onNavigate,
}: MailboxSettingsStorageTabProps) => {
  const { t } = useTranslation();
  const language = i18n.resolvedLanguage;

  // Never surface a bare "0 B"/"0o": zero storage reads as "Empty".
  const formatSize = (bytes: number) =>
    bytes === 0 ? t("Empty") : AttachmentHelper.getFormattedSize(bytes, language);

  const { data: entitlementsData } = useMailboxEntitlements(mailbox.id);
  const { data, isLoading, error } = useMailboxesStorageRetrieve(mailbox.id);

  // `useTrash` shows a toast with a built-in Undo, refreshes the thread lists
  // and refetches these stats — the backend leaves the trashbin out of the
  // list and the trash subtotal has grown — after a trashing and its undo
  // alike. A trashed thread is also dropped from the cached stats at once.
  const queryClient = useQueryClient();
  const { markAsTrashed } = useTrash();
  const storageQueryKey = getMailboxesStorageRetrieveQueryKey(mailbox.id);

  // Rows picked for a bulk move to the trash. Ids no longer listed (trashed,
  // or dropped by a refetch) are ignored rather than pruned, see `selection`.
  const [selectedThreadIds, setSelectedThreadIds] = useState<ReadonlySet<string>>(new Set());

  // The trashed rows unmount with the control that had the focus: it moves to
  // the row now standing at the first trashed row's place (the last one past
  // the end), or to the section title once the list is empty.
  const listRef = useRef<HTMLUListElement>(null);
  const listTitleRef = useRef<HTMLHeadingElement>(null);
  const focusRowIndexRef = useRef<number | null>(null);
  useEffect(() => {
    const index = focusRowIndexRef.current;
    if (index === null) return;
    focusRowIndexRef.current = null;
    const rows = listRef.current?.children ?? [];
    const row = rows[Math.min(index, rows.length - 1)];
    const target = row?.querySelector<HTMLElement>("input:not(:disabled), a[href]") ?? listTitleRef.current;
    target?.focus();
  }, [data]);

  const handleTrash = (threadIds: string[]) => {
    const firstIndex = data?.data.largest_threads.findIndex((thread) => threadIds.includes(thread.id)) ?? -1;
    markAsTrashed({
      threadIds,
      mailboxId: mailbox.id,
      onSuccess: (_, updatedCount) => {
        // The toast already reports a thread the backend did not trash.
        if (updatedCount === 0) return;
        setSelectedThreadIds((selected) => new Set([...selected].filter((id) => !threadIds.includes(id))));
        focusRowIndexRef.current = Math.max(firstIndex, 0);
        queryClient.setQueryData<mailboxesStorageRetrieveResponse>(
          storageQueryKey,
          (response) =>
            response && {
              ...response,
              data: {
                ...response.data,
                largest_threads: response.data.largest_threads.filter(
                  (thread) => !threadIds.includes(thread.id),
                ),
              },
            },
        );
      },
    });
  };

  const toggleThreadSelection = (threadId: string) => {
    setSelectedThreadIds((selected) => {
      const next = new Set(selected);
      if (!next.delete(threadId)) next.add(threadId);
      return next;
    });
  };

  const router = useRouter();
  // The conversation opens in a folder that lists it. The folder's search
  // params have to be set: changing them is what the mailbox provider answers
  // by closing the thread.
  const getThreadLocation = (thread: LargestThread) => ({
    to: "/mailbox/$mailboxId/thread/$threadId" as const,
    params: { mailboxId: mailbox.id, threadId: thread.id },
    search: ViewHelper.getThreadFolderSearch(thread),
  });
  // The native shell has no tabs: a new window would escape the WebView to the
  // system browser, which has no session. There the conversation opens in
  // place, from a plain button rather than a link — a long press on a link
  // offers to open it in that browser too.
  const isNative = isNativePlatform();
  const openThreadLabel = isNative ? t("Open") : t("Open in new tab");
  const getOpenThreadProps = (thread: LargestThread) =>
    isNative
      ? {
          onClick: () => {
            router.navigate(getThreadLocation(thread));
            onNavigate();
          },
        }
      : {
          href: router.buildLocation(getThreadLocation(thread)).href,
          target: "_blank",
          rel: "noopener noreferrer",
        };

  // Modifier/middle clicks open the folder in a new tab — leave the modal open
  // in that case; only a plain left click navigates in place and closes it.
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
    onNavigate();
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

  // Only threads the mailbox can edit can be trashed, hence selected.
  const trashableThreadIds = stats.largest_threads
    .filter((thread) => thread.abilities[Abilities.CAN_EDIT_THREAD])
    .map((thread) => thread.id);
  const selection = trashableThreadIds.filter((id) => selectedThreadIds.has(id));
  const isAllSelected = trashableThreadIds.length > 0 && selection.length === trashableThreadIds.length;
  const isSomeSelected = selection.length > 0;
  const handleSelectAllToggle = () => {
    if (isAllSelected) {
      // Deselecting everything unmounts the selection bar, this checkbox
      // included: hand the focus to the section title before it goes.
      listTitleRef.current?.focus();
      setSelectedThreadIds(new Set());
      return;
    }
    setSelectedThreadIds(new Set(trashableThreadIds));
  };

  const unit = t("GB");
  // The account gauge already shows total-used-over-limit, so its caption *is*
  // the "total storage used" label — no separate headline needed.
  const accountGauge = entitlements
    ? renderGauge(t("Total storage used"), entitlements.account, unit)
    : null;
  // The organization gets no gauge of its own, it only matters once it is full.
  const isOrganizationFull = QuotaHelper.isQuotaReached(entitlements?.organization ?? null);

  return (
    <div className="mailbox-settings__tab mailbox-settings__storage">
      {(isOrganizationFull || accountGauge) && (
        <section className="mailbox-settings__section">
          <div className="mailbox-settings__storage-gauges">
            {isOrganizationFull && (
              <Banner type="warning" compact>
                {t("Domain's storage exceeded")}
              </Banner>
            )}
            {accountGauge}
          </div>
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
          {/* One card per folder, never summed: a message can be both trashed
              and in a spam conversation. Each links to its folder, whose
              banner offers to empty it. */}
          {[
            {
              label: t("Trash"),
              linkLabel: t("View trash"),
              size: stats.trashed_storage,
              search: { has_trashed: "1" },
            },
            {
              label: t("Spam"),
              linkLabel: t("View spam"),
              size: stats.spam_storage,
              search: { is_spam: "1" },
            },
          ].map(({ label, linkLabel, size, search }) => (
            <div
              key={label}
              className="mailbox-settings__storage-metric mailbox-settings__storage-metric--with-action"
            >
              <span className="mailbox-settings__storage-metric-value">
                {formatSize(size)}
              </span>
              <span className="mailbox-settings__storage-metric-label">
                {label}
              </span>
              <Tooltip content={linkLabel}>
                <ButtonLink
                  to="/mailbox/$mailboxId"
                  params={{ mailboxId: mailbox.id }}
                  search={search}
                  className="mailbox-settings__storage-metric-action"
                  size="small"
                  variant="tertiary"
                  icon={<Icon name="open_in_new" type={IconType.OUTLINED} />}
                  aria-label={linkLabel}
                  onClick={handleFollowLink}
                />
              </Tooltip>
            </div>
          ))}
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
            <h3 ref={listTitleRef} tabIndex={-1} className="mailbox-settings__section-title">
              {t("Threads using the most storage")}
            </h3>
          </header>

          {/* Always rendered, empty without a selection: a live region has to
              exist before its content changes for the count to be announced.
              Selecting everything starts from one row, as the use case is rare
              on a list of the largest threads. */}
          <div
            className={clsx("mailbox-settings__storage-selection-bar", {
              "mailbox-settings__storage-selection-bar--active": isSomeSelected,
            })}
          >
            <div className="mailbox-settings__storage-selection">
              {isSomeSelected && (
                <Checkbox
                  checked={isAllSelected}
                  indeterminate={!isAllSelected}
                  onChange={handleSelectAllToggle}
                  aria-label={isAllSelected ? t("Deselect all threads") : t("Select all threads")}
                />
              )}
              <p role="status" className="mailbox-settings__storage-selection-count">
                {isSomeSelected ? t("{{count}} selected threads", { count: selection.length }) : null}
              </p>
            </div>
            {isSomeSelected && (
              <Button
                size="small"
                variant="secondary"
                icon={<Icon name="delete" type={IconType.OUTLINED} />}
                onClick={() => handleTrash(selection)}
              >
                {t("Move to trash")}
              </Button>
            )}
          </div>

          <ul ref={listRef} className="mailbox-settings__storage-list">
            {stats.largest_threads.map((thread) => {
              const canTrash = thread.abilities[Abilities.CAN_EDIT_THREAD];
              const isSelected = canTrash && selectedThreadIds.has(thread.id);
              const ids = {
                subject: `storage-thread-${thread.id}-subject`,
                status: `storage-thread-${thread.id}-status`,
                meta: `storage-thread-${thread.id}-meta`,
                size: `storage-thread-${thread.id}-size`,
              };
              const summary = (
                <>
                  <span
                    id={ids.status}
                    role="img"
                    className="mailbox-settings__storage-item-status"
                    aria-label={thread.is_unread ? t("Unread") : t("Read")}
                  />
                  <span className="mailbox-settings__storage-item-main">
                    <span id={ids.subject} className="mailbox-settings__storage-item-subject">
                      {thread.subject || t("No subject")}
                    </span>
                    <span id={ids.meta} className="mailbox-settings__storage-item-meta">
                      {t("{{count}} messages", { count: thread.message_count })}
                      {thread.messaged_at
                        ? ` · ${DateHelper.formatDate(thread.messaged_at, language, false)}`
                        : ""}
                    </span>
                  </span>
                  <span id={ids.size} className="mailbox-settings__storage-item-size">
                    {formatSize(thread.size)}
                  </span>
                </>
              );
              return (
                <li
                  key={thread.id}
                  className={clsx("mailbox-settings__storage-item", {
                    "mailbox-settings__storage-item--unread": thread.is_unread,
                    "mailbox-settings__storage-item--selected": isSelected,
                  })}
                >
                  {/* The whole row is the checkbox label, so a click anywhere
                      on it selects the conversation. A thread shared with the
                      mailbox as a viewer cannot be trashed, hence selected:
                      its checkbox and trash slots are kept empty so rows stay
                      aligned. */}
                  {canTrash ? (
                    <Checkbox
                      className="mailbox-settings__storage-item-select"
                      checked={isSelected}
                      onChange={() => toggleThreadSelection(thread.id)}
                      label={summary}
                      fullWidth
                      // Named by the subject alone, the rest of the row describes it: the
                      // label text would read it all as one long name.
                      aria-labelledby={ids.subject}
                      aria-describedby={`${ids.status} ${ids.meta} ${ids.size}`}
                    />
                  ) : (
                    <div className="mailbox-settings__storage-item-select mailbox-settings__storage-item-select--readonly">
                      <span className="mailbox-settings__storage-item-checkbox-slot" />
                      {summary}
                    </div>
                  )}
                  <Tooltip content={t("Move to trash")}>
                    <Button
                      className={clsx("mailbox-settings__storage-item-action", {
                        "mailbox-settings__storage-item-action--hidden": !canTrash,
                      })}
                      size="nano"
                      variant="tertiary"
                      aria-label={t("Move to trash")}
                      aria-describedby={ids.subject}
                      icon={<Icon name="delete" type={IconType.OUTLINED} />}
                      disabled={!canTrash}
                      onClick={() => handleTrash([thread.id])}
                    />
                  </Tooltip>
                  <Tooltip content={openThreadLabel}>
                    <Button
                      className="mailbox-settings__storage-item-action"
                      size="nano"
                      variant="tertiary"
                      aria-label={openThreadLabel}
                      aria-describedby={ids.subject}
                      icon={<Icon name={isNative ? "chevron_right" : "open_in_new"} type={IconType.OUTLINED} />}
                      {...getOpenThreadProps(thread)}
                    />
                  </Tooltip>
                </li>
              );
            })}
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
  // The usage summary below still conveys how much is stored.
  if (!QuotaHelper.hasLimit(level)) {
    return null;
  }
  return (
    <StorageGaugeInformation
      title={caption}
      used={QuotaHelper.toGigabytes(level.storage_used)}
      total={QuotaHelper.toGigabytes(level.max_storage)}
      unit={unit}
    />
  );
};

import { DropdownMenu, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { ChevronDown } from "@gouvfr-lasuite/ui-kit/icons";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Mailbox } from "@/features/api/gen";
import MailboxHelper from "@/features/utils/mailbox-helper";

/** Display name of a mailbox, falling back to its address when no contact name
 * is set (a mailbox may legitimately have a null/blank name). */
const getMailboxLabel = (mailbox: Mailbox) => mailbox.name?.trim() || mailbox.email;

type MailboxSelectorProps = {
  /** Mailboxes the user can switch to (already the eligible subset). */
  mailboxes: readonly Mailbox[];
  /** Currently displayed mailbox. */
  selectedMailbox: Mailbox;
  /** Called with the picked mailbox id; never fired for the current one. */
  onSelect: (mailboxId: string) => void | Promise<void>;
};

/**
 * Mailbox switcher shared by the sidebar header and the settings modal: an
 * avatar + bold name + address card that unfolds a dropdown of the eligible
 * mailboxes. When the user owns a single mailbox there is nothing to switch to,
 * so the card renders as static content (not a disabled button) to keep the
 * name/address legible and avoid exposing a bogus "button, unavailable" control.
 */
export const MailboxSelector = ({
  mailboxes,
  selectedMailbox,
  onSelect,
}: MailboxSelectorProps) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  const label = getMailboxLabel(selectedMailbox);
  const sublabel = selectedMailbox.name?.trim() ? selectedMailbox.email : null;
  const canSwitch = mailboxes.length > 1;

  // Avatar is decorative: the name it encodes is already shown next to it as
  // text, so hide it from assistive tech to avoid a duplicate announcement.
  const content = (
    <>
      <span
        className="mailbox-selector__avatar"
        data-shared={!selectedMailbox.is_identity}
        aria-hidden="true"
      >
        <UserAvatar fullName={label} size="small" />
      </span>
      <span className="mailbox-selector__text">
        <span className="mailbox-selector__name">{label}</span>
        {sublabel && <span className="mailbox-selector__email">{sublabel}</span>}
      </span>
    </>
  );

  if (!canSwitch) {
    return (
      <div className="mailbox-selector">
        <div className="mailbox-selector__trigger mailbox-selector__trigger--static">
          {content}
        </div>
      </div>
    );
  }

  const sortedMailboxes = MailboxHelper.sortByKind(mailboxes);
  const options = sortedMailboxes.map((mailbox, index) => ({
    label: (<div className="mailbox-selector__option-label">
      <span className="mailbox-selector__option-name">
        {getMailboxLabel(mailbox)}
      </span>
      {mailbox.id !== selectedMailbox.id && mailbox.count_unread_threads > 0 && (
            <span
              className="mailbox-selector__option-unread-count"
              aria-label={t("{{count}} unread", { count: mailbox.count_unread_threads })}
            >
              {mailbox.count_unread_threads > 9999 ? "9999+" : mailbox.count_unread_threads}
            </span>
          )}
      </div>) as unknown as string,
    subText: mailbox.name?.trim() ? mailbox.email : undefined,
    value: mailbox.id,
    // The dropdown option type has no right-side slot (label/subText are plain
    // strings), so the unread counter piggybacks on the icon ReactNode and is
    // moved to the trailing edge in CSS.
    icon: (
        <span
          className="mailbox-selector__option-avatar"
          data-shared={!mailbox.is_identity}
        >
          <UserAvatar fullName={getMailboxLabel(mailbox)} size="small" />
        </span>
    ),
    showSeparator: MailboxHelper.showSeparatorAfter(sortedMailboxes, index),
  }));

  return (
    <div className="mailbox-selector">
      <DropdownMenu
        options={options}
        isOpen={isOpen}
        onOpenChange={setIsOpen}
        selectedValues={[selectedMailbox.id]}
        onSelectValue={(value) => {
          setIsOpen(false);
          if (value !== selectedMailbox.id) {
            void onSelect(value);
          }
        }}
      >
        <Button
          className="mailbox-selector__trigger"
          color="brand"
          variant="tertiary"
          icon={<ChevronDown />}
          iconPosition="right"
          aria-haspopup="menu"
          aria-expanded={isOpen}
          fullWidth
          onClick={() => setIsOpen(!isOpen)}
        >
          {content}
        </Button>
      </DropdownMenu>
    </div>
  );
};

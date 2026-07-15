import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { Mailbox, useMailboxesImportsList } from "@/features/api/gen";
import { ImportsDataGrid } from "../../imports-view/imports-data-grid";
import { ResourceSectionHeader } from "../resource-section-header";

// Open the importer via the URL hash rather than useModalStore/openModal:
// providers/modal-store side-effect-imports the controlled-modals registry,
// which imports this settings-modal module — importing it from here (a child of
// that module) closes a circular import and trips a TDZ ("Cannot access
// 'MODAL_MAILBOX_SETTINGS_ID' before initialization"). The header and
// empty-thread openers use the same hash for the same reason.
const openMessageImporter = () => {
  window.location.hash = "#modal-message-importer";
};

type MailboxSettingsImportsTabProps = {
  mailbox: Mailbox;
};

export const MailboxSettingsImportsTab = ({
  mailbox,
}: MailboxSettingsImportsTabProps) => {
  const { t } = useTranslation();
  const { data } = useMailboxesImportsList(mailbox.id, {
    query: { enabled: !!mailbox.id },
  });
  const count = data?.data.length;

  return (
    <div className="mailbox-settings__tab mailbox-settings__imports">
      <section className="mailbox-settings__section">
        <ResourceSectionHeader
          label={
            count === undefined
              ? undefined
              : count === 0
                ? t("No imports")
                : t("{{count}} import", { count })
          }
          action={
            <Button
              size="nano"
              icon={<Icon name="add" />}
              onClick={openMessageImporter}
            >
              {t("New import")}
            </Button>
          }
        />
        <ImportsDataGrid mailbox={mailbox} />
      </section>
    </div>
  );
};

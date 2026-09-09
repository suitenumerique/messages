import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import {
  Mailbox,
  getMailboxesImportsListQueryKey,
  useMailboxesImportsList,
} from "@/features/api/gen";
import { Icon } from "@/features/ui/components/icon";
import { Plus } from "@gouvfr-lasuite/ui-kit/icons";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ImportsDataGrid } from "../../imports-view/imports-data-grid";
import { ImportNewView } from "../../imports-view/import-new-view";
import { ResourceSectionHeader } from "../resource-section-header";

export type ImportsTabView = "list" | "new";

type MailboxSettingsImportsTabProps = {
  mailbox: Mailbox;
  // View is owned by the settings modal so it can swap the tab title/subtitle
  // for the compact "new import" sub-view (no list description pushing the
  // submit button below the fold).
  view: ImportsTabView;
  onViewChange: (view: ImportsTabView) => void;
};

export const MailboxSettingsImportsTab = ({
  mailbox,
  view,
  onViewChange,
}: MailboxSettingsImportsTabProps) => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { isMobile } = useResponsive();
  const { data } = useMailboxesImportsList(mailbox.id, {
    query: { enabled: !!mailbox.id },
  });
  const count = data?.data.length;

  const invalidateImports = () =>
    queryClient.invalidateQueries({
      queryKey: getMailboxesImportsListQueryKey(mailbox.id),
      exact: false,
    });

  // The run was created and is processing server-side: surface it in the list
  // and reassure the user they don't have to wait here for it to finish.
  const handleImportStarted = () => {
    onViewChange("list");
    invalidateImports();
    addToast(
      <ToasterItem type="info">
        <span>
          {t(
            "Import started. You can close this window — it will keep running in the background.",
          )}
        </span>
      </ToasterItem>,
    );
  };

  if (view === "new") {
    return (
      <div className="mailbox-settings__tab mailbox-settings__imports">
        <ImportNewView mailboxId={mailbox.id} onStarted={handleImportStarted} />
      </div>
    );
  }

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
              size={isMobile ? "small" : "nano"}
              icon={<Icon icon={Plus} />}
              onClick={() => onViewChange("new")}
              aria-label={isMobile ? t("New import") : undefined}
            >
              {!isMobile && t("New import")}
            </Button>
          }
        />
        <ImportsDataGrid mailbox={mailbox} />
      </section>
    </div>
  );
};

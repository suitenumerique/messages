import { useTranslation } from "react-i18next";
import { Mailbox, useMailboxesChannelsList } from "@/features/api/gen";
import { CreateIntegrationAction } from "../../integrations-view/create-integration-action";
import { IntegrationsDataGrid } from "../../integrations-view/integrations-data-grid";
import { ResourceSectionHeader } from "../resource-section-header";

type MailboxSettingsIntegrationsTabProps = {
  mailbox: Mailbox;
};

export const MailboxSettingsIntegrationsTab = ({
  mailbox,
}: MailboxSettingsIntegrationsTabProps) => {
  const { t } = useTranslation();
  const { data } = useMailboxesChannelsList(mailbox.id, {
    query: { enabled: !!mailbox.id },
  });
  const count = data?.data.length;

  return (
    <div className="mailbox-settings__tab mailbox-settings__integrations">
      <section className="mailbox-settings__section">
        <ResourceSectionHeader
          label={
            count === undefined
              ? undefined
              : count === 0
                ? t("No integration")
                : t("{{count}} integration", { count })
          }
          action={<CreateIntegrationAction mailbox={mailbox} size="nano" />}
        />
        <IntegrationsDataGrid mailbox={mailbox} />
      </section>
    </div>
  );
};

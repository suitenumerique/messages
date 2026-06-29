import { useTranslation } from "react-i18next";
import {
  Mailbox,
  MessageTemplateTypeChoices,
  useMailboxesMessageTemplatesList,
} from "@/features/api/gen";
import { ComposeTemplateAction } from "../../message-templates-view/compose-template-action";
import { MessageTemplateDataGrid } from "../../message-templates-view/message-template-data-grid";
import { ResourceSectionHeader } from "../resource-section-header";

type MailboxSettingsMessageTemplatesTabProps = {
  mailbox: Mailbox;
};

export const MailboxSettingsMessageTemplatesTab = ({
  mailbox,
}: MailboxSettingsMessageTemplatesTabProps) => {
  const { t } = useTranslation();
  const { data } = useMailboxesMessageTemplatesList(
    mailbox.id,
    { type: [MessageTemplateTypeChoices.message] },
    { query: { enabled: !!mailbox.id } },
  );
  const count = data?.data.length;

  return (
    <div className="mailbox-settings__tab mailbox-settings__message-templates">
      <section className="mailbox-settings__section">
        <ResourceSectionHeader
          label={
            count === undefined
              ? undefined
              : count === 0
                ? t("No message template")
                : t("{{count}} message template", { count })
          }
          action={<ComposeTemplateAction mailbox={mailbox} size="nano" />}
        />
        <MessageTemplateDataGrid mailbox={mailbox} />
      </section>
    </div>
  );
};

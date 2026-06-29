import { useTranslation } from "react-i18next";
import {
  Mailbox,
  MessageTemplateTypeChoices,
  useMailboxesMessageTemplatesList,
} from "@/features/api/gen";
import { ComposeAutoreplyAction } from "../../autoreplies-view/compose-autoreply-action";
import { AutoreplyDataGrid } from "../../autoreplies-view/autoreply-data-grid";
import { ResourceSectionHeader } from "../resource-section-header";

type MailboxSettingsAutorepliesTabProps = {
  mailbox: Mailbox;
};

export const MailboxSettingsAutorepliesTab = ({
  mailbox,
}: MailboxSettingsAutorepliesTabProps) => {
  const { t } = useTranslation();
  const { data } = useMailboxesMessageTemplatesList(
    mailbox.id,
    { type: [MessageTemplateTypeChoices.autoreply] },
    { query: { enabled: !!mailbox.id } },
  );
  const count = data?.data.length;

  return (
    <div className="mailbox-settings__tab mailbox-settings__autoreplies">
      <section className="mailbox-settings__section">
        <ResourceSectionHeader
          label={
            count === undefined
              ? undefined
              : count === 0
                ? t("No auto-reply")
                : t("{{count}} auto-reply", { count })
          }
          action={<ComposeAutoreplyAction mailbox={mailbox} size="nano" />}
        />
        <AutoreplyDataGrid mailbox={mailbox} />
      </section>
    </div>
  );
};

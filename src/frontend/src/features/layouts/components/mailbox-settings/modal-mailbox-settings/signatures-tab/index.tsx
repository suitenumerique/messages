import { useTranslation } from "react-i18next";
import {
  Mailbox,
  MessageTemplateTypeChoices,
  useMailboxesMessageTemplatesList,
} from "@/features/api/gen";
import { ComposeSignatureAction } from "../../signatures-view/compose-signature-action";
import { SignatureDataGrid } from "../../signatures-view/signature-data-grid";
import { ResourceSectionHeader } from "../resource-section-header";

type MailboxSettingsSignaturesTabProps = {
  mailbox: Mailbox;
};

export const MailboxSettingsSignaturesTab = ({
  mailbox,
}: MailboxSettingsSignaturesTabProps) => {
  const { t } = useTranslation();
  const { data } = useMailboxesMessageTemplatesList(
    mailbox.id,
    { type: [MessageTemplateTypeChoices.signature] },
    { query: { enabled: !!mailbox.id } },
  );
  const count = data?.data.length;

  return (
    <div className="mailbox-settings__tab mailbox-settings__signatures">
      <section className="mailbox-settings__section">
        <ResourceSectionHeader
          label={
            count === undefined
              ? undefined
              : count === 0
                ? t("No signatures")
                : t("{{count}} signature", { count })
          }
          action={<ComposeSignatureAction mailbox={mailbox} size="nano" />}
        />
        <SignatureDataGrid mailbox={mailbox} />
      </section>
    </div>
  );
};

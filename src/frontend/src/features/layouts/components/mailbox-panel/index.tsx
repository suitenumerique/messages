import { HorizontalSeparator, Spinner } from "@gouvfr-lasuite/ui-kit"
import { MailboxPanelActions } from "./components/mailbox-actions"
import { MailboxList } from "./components/mailbox-list"
import { useMailboxContext } from "@/features/mailbox/provider";
import { Select } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";

export const MailboxPanel = () => {
    const { t } = useTranslation();
    const { selectedMailbox, mailboxes, status} = useMailboxContext();

    const getMailboxOptions = () => {
        if(!mailboxes) return [];
        return mailboxes.map((mailbox) => ({
            label: mailbox.email,
            value: mailbox.id
        }));
    }

    return (
        <div className="mailbox-panel">
            <div className="mailbox-panel__header">
                <MailboxPanelActions />
                <HorizontalSeparator withPadding={false} />
            </div>
            {!selectedMailbox || status.mailboxes === "pending" ? <Spinner /> : 
            (
                <>
                    {/* FIXME: For now we consider user has always only one mailbox */}
                    <Select
                        className="mailbox-panel__mailbox-title"
                        options={getMailboxOptions()}
                        defaultValue={selectedMailbox.id}
                        label={t('mailbox')}
                        clearable={false}
                        compact
                        fullWidth
                        showLabelWhenSelected={false}
                    />
                    <MailboxList />
                </>
            )}
        </div>
    )
}
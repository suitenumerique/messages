import { HorizontalSeparator, Spinner } from "@gouvfr-lasuite/ui-kit"
import { MailboxPanelActions } from "./components/mailbox-actions"
import { MailboxList } from "./components/mailbox-list"
import { useMailboxContext } from "@/features/providers/mailbox";
import { Select } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { useSearchParams } from "next/navigation";
import { useLayoutContext } from "../main";

export const MailboxPanel = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const searchParams = useSearchParams();
    const { selectedMailbox, mailboxes, queryStates } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();

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
            {!selectedMailbox || queryStates.mailboxes.isLoading ? <Spinner /> : 
            (
                <>
                    <Select
                        className="mailbox-panel__mailbox-title"
                        options={getMailboxOptions()}
                        value={selectedMailbox.id}
                        label={t('mailbox')}
                        onChange={(event) => {
                            closeLeftPanel();
                            router.push(`/mailbox/${event.target.value}?${searchParams.toString()}`);
                        }}
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

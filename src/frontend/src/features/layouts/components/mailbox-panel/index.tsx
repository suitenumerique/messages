import { HorizontalSeparator, Spinner } from "@gouvfr-lasuite/ui-kit"
import { MailboxPanelActions } from "./components/mailbox-actions"
import { MailboxList } from "./components/mailbox-list"
import { useMailboxContext } from "@/features/mailbox/provider";

export const MailboxPanel = () => {
    const { selectedMailbox, status} = useMailboxContext();
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
                    <h2 className="mailbox-panel__mailbox-title">{selectedMailbox.email}</h2>
                    <MailboxList />
                </>
            )}
        </div>
    )
}
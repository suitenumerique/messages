import { DropdownMenu, HorizontalSeparator, Icon, Spinner } from "@gouvfr-lasuite/ui-kit"
import { MailboxPanelActions } from "./components/mailbox-actions"
import { MailboxList } from "./components/mailbox-list"
import { useMailboxContext } from "@/features/providers/mailbox";
import { Button } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useSearchParams } from "next/navigation";
import { useLayoutContext } from "../main";
import { MailboxLabels } from "./components/mailbox-labels";
import { useState } from "react";

export const MailboxPanel = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const { selectedMailbox, mailboxes, queryStates } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const [isOpen, setIsOpen] = useState(false);

    const getMailboxOptions = () => {
        if (!mailboxes) return [];
        return mailboxes.map((mailbox) => ({
            label: mailbox.email,
            value: mailbox.id,
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
                        <div className="mailbox-panel__mailbox-title">
                            <DropdownMenu
                                options={getMailboxOptions()}
                                isOpen={isOpen}
                                onOpenChange={setIsOpen}
                                selectedValues={[selectedMailbox.id]}
                                onSelectValue={(value) => {
                                    closeLeftPanel();
                                    router.push(`/mailbox/${value}?${searchParams.toString()}`);
                                }}
                            >
                                <Button
                                    className="mailbox-panel__mailbox-title__dropdown-button"
                                    color="tertiary-text"
                                    icon={<Icon name={isOpen ? "arrow_drop_up" : "arrow_drop_down"} />}
                                    iconPosition="right"
                                    onClick={() => setIsOpen(!isOpen)}
                                >
                                    <span className="button__label">{selectedMailbox.email}</span>
                                </Button>
                            </DropdownMenu>
                        </div>
                        <MailboxList />
                        <MailboxLabels mailbox={selectedMailbox} />
                    </>
                )}
        </div>
    )
}

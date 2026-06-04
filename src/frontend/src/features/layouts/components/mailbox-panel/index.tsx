import { DropdownMenu, HorizontalSeparator, Icon, Spinner } from "@gouvfr-lasuite/ui-kit"
import { MailboxPanelActions } from "./components/mailbox-actions"
import { MailboxList } from "./components/mailbox-list"
import { useMailboxContext } from "@/features/providers/mailbox";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useNavigate } from "@tanstack/react-router";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import { MailboxLabels } from "./components/mailbox-labels";
import { useState } from "react";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";

export const MailboxPanel = () => {
    const navigate = useNavigate();
    const searchParams = useUrlSearchParams();
    const { selectedMailbox, mailboxes, queryStates } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const [isOpen, setIsOpen] = useState(false);
    const { defaultLayout, onLayoutChange } = useDefaultLayout({
        groupId: "mailbox-panel-sections",
        storage: typeof window !== "undefined" ? localStorage : undefined,
    });

    const getMailboxOptions = () => {
        if (!mailboxes) return [];
        const sortedMailboxes = [...mailboxes].sort((a, b) => {
            const identityDiff = Number(b.is_identity) - Number(a.is_identity)
            if (identityDiff !== 0) return identityDiff;
            return a.email.localeCompare(b.email)
        })
        return sortedMailboxes.map((mailbox, index) => ({
            label: mailbox.email,
            value: mailbox.id,
            icon: mailbox.is_identity ? <Icon name="person" /> : <Icon name="group" />,
            showSeparator: mailbox.is_identity && (sortedMailboxes[index + 1] && !sortedMailboxes[index + 1].is_identity)
        }));
    }

    return (
        <div className="mailbox-panel">
            <div className="mailbox-panel__header">
                <MailboxPanelActions />
                <HorizontalSeparator withPadding={false} />
                { selectedMailbox && (
                <div className="mailbox-panel__mailbox-title">
                            <DropdownMenu
                                options={getMailboxOptions()}
                                isOpen={isOpen}
                                onOpenChange={setIsOpen}
                                selectedValues={[selectedMailbox.id]}
                                onSelectValue={(value) => {
                                    closeLeftPanel();
                                    navigate({ to: '/mailbox/$mailboxId', params: { mailboxId: value }, search: Object.fromEntries(searchParams) });
                                }}
                            >
                                <Button
                                    className="mailbox-panel__mailbox-title__dropdown-button"
                                    color="neutral"
                                    variant="tertiary"
                                    icon={<Icon name={isOpen ? "arrow_drop_up" : "arrow_drop_down"} />}
                                    iconPosition="right"
                                    onClick={() => setIsOpen(!isOpen)}
                                >
                                    <span className="button__label">{selectedMailbox.email}</span>
                                </Button>
                            </DropdownMenu>
                        </div>
                )}
            </div>
            {!selectedMailbox || queryStates.mailboxes.isLoading ? <Spinner /> :
                (
                    <Group
                        orientation="vertical"
                        className="mailbox-panel__body"
                        defaultLayout={defaultLayout}
                        onLayoutChange={onLayoutChange}
                    >
                        <Panel id="mailbox-panel-folders" defaultSize="40%" minSize="20%">
                            <MailboxList />
                        </Panel>
                        <Separator className="panel__resize-handle" />
                        <Panel id="mailbox-panel-labels" defaultSize="60%" minSize="20%">
                            <MailboxLabels mailbox={selectedMailbox} />
                        </Panel>
                    </Group>
                )}
        </div>
    )
}

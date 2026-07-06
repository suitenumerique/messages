import { HorizontalSeparator, Spinner } from "@gouvfr-lasuite/ui-kit"
import { MailboxPanelActions } from "./components/mailbox-actions"
import { MailboxList } from "./components/mailbox-list"
import { useMailboxContext } from "@/features/providers/mailbox";
import { useNavigate } from "@tanstack/react-router";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import { MailboxLabels } from "./components/mailbox-labels";
import { MAILBOX_FOLDERS } from "./components/mailbox-list";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import { MailboxSelector } from "@/features/layouts/components/mailbox-selector";

export const MailboxPanel = () => {
    const navigate = useNavigate();
    const searchParams = useUrlSearchParams();
    const { selectedMailbox, mailboxes, queryStates } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const { defaultLayout, onLayoutChange } = useDefaultLayout({
        groupId: "mailbox-panel-sections",
        storage: typeof window !== "undefined" ? localStorage : undefined,
    });

    return (
        <div className="mailbox-panel">
            <div className="mailbox-panel__header">
                <MailboxPanelActions />
                <HorizontalSeparator withPadding={false} />
                { selectedMailbox && mailboxes && (
                    <div className="mailbox-panel__mailbox-title">
                        <MailboxSelector
                            mailboxes={mailboxes}
                            selectedMailbox={selectedMailbox}
                            onSelect={(mailboxId) => {
                                closeLeftPanel();
                                const search = searchParams.has("search")
                                    ? MAILBOX_FOLDERS()[0].filter
                                    : Object.fromEntries(searchParams);
                                navigate({ to: '/mailbox/$mailboxId', params: { mailboxId }, search });
                            }}
                        />
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

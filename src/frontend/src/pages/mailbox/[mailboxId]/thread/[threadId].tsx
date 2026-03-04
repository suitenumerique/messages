import { MainLayout } from "@/features/layouts/components/main";
import { ThreadPanel } from "@/features/layouts/components/thread-panel";
import { ThreadSelectionPlaceholder } from "@/features/layouts/components/thread-selection-placeholder";
import { ThreadView } from "@/features/layouts/components/thread-view";
import { ThreadSelectionProvider, useThreadSelection } from "@/features/providers/thread-selection";
import { Panel, Group, Separator, useDefaultLayout } from "react-resizable-panels";

const Mailbox = () => {
    const { selectedThreadIds } = useThreadSelection();
    const { defaultLayout, onLayoutChange } = useDefaultLayout({
        groupId: "threads",
        storage: localStorage,
    });

    return (
        <Group defaultLayout={defaultLayout} onLayoutChange={onLayoutChange} orientation="horizontal" className="threads__container">
            <Panel id="panel-thread-list" className="thread-list-panel" defaultSize="30%" minSize="250px" maxSize="50%">
                <ThreadPanel />
            </Panel>
            <Separator className="panel__resize-handle" />
            <Panel id="panel-thread-view" className="thread-view-panel">
                {selectedThreadIds.size > 0 ? (
                    <ThreadSelectionPlaceholder />
                ) : (
                    <ThreadView />
                )}
            </Panel>
        </Group>
    );
};

Mailbox.getLayout = function getLayout(page: React.ReactElement) {
    return (
        <MainLayout>
            <ThreadSelectionProvider>
                {page}
            </ThreadSelectionProvider>
        </MainLayout>
    )
}

export default Mailbox;

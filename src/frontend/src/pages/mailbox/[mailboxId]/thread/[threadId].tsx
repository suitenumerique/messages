import { MainLayout } from "@/features/layouts/components/main";
import { ThreadPanel } from "@/features/layouts/components/thread-panel";
import { ThreadView } from "@/features/layouts/components/thread-view";
import { Panel, Group, Separator, useDefaultLayout } from "react-resizable-panels";

const Mailbox = () => {
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
                <ThreadView />
            </Panel>
        </Group>
    )
}

Mailbox.getLayout = function getLayout(page: React.ReactElement) {
    return (
        <MainLayout>
            {page}
        </MainLayout>
    )
}

export default Mailbox;

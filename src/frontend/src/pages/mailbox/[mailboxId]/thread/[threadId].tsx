import { MainLayout } from "@/features/layouts/components/main";
import { ThreadPanel } from "@/features/layouts/components/thread-panel";
import { ThreadView } from "@/features/layouts/components/thread-view";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

const Mailbox = () => {
    return (
        <PanelGroup autoSaveId="threads" direction="horizontal" className="threads__container">
            <Panel className="thread-list-panel" defaultSize={50} minSize={25}>
                <ThreadPanel />
            </Panel>
            <PanelResizeHandle className="thread__resize-handle" />
            <Panel className="thread-view-panel" defaultSize={50} minSize={60}>
                <ThreadView />
            </Panel>
        </PanelGroup>
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
import { MainLayout } from "@/features/layouts/components/main";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { ThreadPanel } from "@/features/layouts/components/thread-panel";
import { ThreadSelectionPlaceholder } from "@/features/layouts/components/thread-selection-placeholder";
import { useMailboxContext } from "@/features/providers/mailbox";
import { ThreadSelectionProvider, useThreadSelection } from "@/features/providers/thread-selection";
import Image from "next/image";
import { useTranslation } from "react-i18next";
import { Panel, Group, Separator, useDefaultLayout } from "react-resizable-panels";

const Mailbox = () => {
    const { t } = useTranslation();
    const { threads } = useMailboxContext();
    const { selectedThreadIds } = useThreadSelection();
    const { isMobile } = useResponsive();
    const showSelectThreadPlaceholder = (!isMobile && (threads?.results?.length ?? 0) > 0);
    const { defaultLayout, onLayoutChange } = useDefaultLayout({
        groupId: showSelectThreadPlaceholder ? "threads" : "threads-single",
        storage: localStorage,
    });

    return (
        <Group defaultLayout={defaultLayout} onLayoutChange={onLayoutChange} orientation="horizontal" className="threads__container">
            <Panel id={showSelectThreadPlaceholder ? "panel-thread-list" : "panel-thread-list-single"} className="thread-list-panel" defaultSize="35%" minSize="20%" maxSize="50%">
                <ThreadPanel />
            </Panel>
            {showSelectThreadPlaceholder && (
                <>
                    <Separator className="panel__resize-handle" />
                    <Panel id="panel-thread-view" className="thread-view-panel">
                        {selectedThreadIds.size > 0 ? (
                            <ThreadSelectionPlaceholder />
                        ) : (
                            <div className="thread-view thread-view--empty">
                                <div>
                                    <Image src="/images/svg/read-mail.svg" alt="" width={60} height={60} />
                                    <p>{t('Select a thread')}</p>
                                </div>
                            </div>
                        )}
                    </Panel>
                </>
            )}
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

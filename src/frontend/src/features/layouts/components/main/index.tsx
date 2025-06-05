import { AppLayout } from "./layout";
import { createContext, PropsWithChildren, useContext, useState } from "react";
import { GlobalLayout } from "../global/global-layout";
import AuthenticatedView from "./authenticated-view";
import { MailboxProvider, useMailboxContext } from "@/features/providers/mailbox";
import { NoMailbox } from "./no-mailbox";
import { Header } from "./header";
import { Toaster } from "@/features/ui/components/toaster";
import { SentBoxProvider } from "@/features/providers/sent-box";
import { LeftPanel } from "./left-panel";
import { ModalStoreProvider } from "@/features/providers/modal-store";

export const MainLayout = ({ children }: PropsWithChildren) => {
    return (
        <GlobalLayout>
            <AuthenticatedView>
                <ModalStoreProvider>
                <MailboxProvider>
                    <SentBoxProvider>
                        <MainLayoutContent>{children}</MainLayoutContent>
                        <Toaster />
                    </SentBoxProvider>
                    </MailboxProvider>
                </ModalStoreProvider>
            </AuthenticatedView>
        </GlobalLayout>
    )
}

const LayoutContext = createContext({
    toggleLeftPanel: () => {},
    closeLeftPanel: () => {},
    openLeftPanel: () => {},
})

const MainLayoutContent = ({ children }: PropsWithChildren) => {
    const { mailboxes, queryStates } = useMailboxContext();
    const hasNoMailbox = queryStates.mailboxes.status === 'success' && mailboxes!.length === 0;
    const [leftPanelOpen, setLeftPanelOpen] = useState(false);

    if (hasNoMailbox) {
        return (
            <>
                <Header />
                <NoMailbox />
            </>
        )
    }

    return (
        <LayoutContext.Provider value={{
            toggleLeftPanel: () => setLeftPanelOpen(!leftPanelOpen),
            closeLeftPanel: () => setLeftPanelOpen(false),
            openLeftPanel: () => setLeftPanelOpen(true),
        }}>
            <AppLayout
                enableResize
                isLeftPanelOpen={leftPanelOpen}
                setIsLeftPanelOpen={setLeftPanelOpen}
                leftPanelContent={<LeftPanel />}
                icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
            >
                {children}
            </AppLayout>
        </LayoutContext.Provider>
    )
}

export const useLayoutContext = () => {
    const context = useContext(LayoutContext);
    if (!context) throw new Error("useLayoutContext must be used within a LayoutContext.Provider");
    return useContext(LayoutContext)
}

"use client";
import { AppLayout } from "./layout";
import { createContext, PropsWithChildren, useContext, useState } from "react";
import { GlobalLayout } from "../global/global-layout";
import AuthenticatedView from "./authenticated-view";
import { MailboxProvider, useMailboxContext } from "@/features/providers/mailbox";
import { NoMailbox } from "./no-mailbox";
import { Toaster } from "@/features/ui/components/toaster";
import { SentBoxProvider } from "@/features/providers/sent-box";
import { LeftPanel } from "./left-panel";
import { ModalStoreProvider } from "@/features/providers/modal-store";

export const MainLayout = ({ children, simple = false }: PropsWithChildren<{ simple?: boolean }>) => {
    return (
        <GlobalLayout>
            <AuthenticatedView>
                    <MailboxProvider>
                        <SentBoxProvider>
                            <ModalStoreProvider>
                                <MainLayoutContent simple={simple}>{children}</MainLayoutContent>
                                <Toaster />
                            </ModalStoreProvider>
                        </SentBoxProvider>
                    </MailboxProvider>
            </AuthenticatedView>
        </GlobalLayout>
    )
}

const LayoutContext = createContext({
    toggleLeftPanel: () => {},
    closeLeftPanel: () => {},
    openLeftPanel: () => {},
})

const MainLayoutContent = ({ children }: PropsWithChildren<{ simple?: boolean }>) => {
    const { mailboxes, queryStates } = useMailboxContext();
    const hasNoMailbox = queryStates.mailboxes.status === 'success' && mailboxes!.length === 0;
    const [leftPanelOpen, setLeftPanelOpen] = useState(false);

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
                leftPanelContent={<LeftPanel hasNoMailbox={hasNoMailbox} />}
                icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
            >
                {hasNoMailbox ? (
                    <NoMailbox />
                ) : (
                    children
                )}
            </AppLayout>
        </LayoutContext.Provider>
    )
}

export const useLayoutContext = () => {
    const context = useContext(LayoutContext);
    if (!context) throw new Error("useLayoutContext must be used within a LayoutContext.Provider");
    return useContext(LayoutContext)
}

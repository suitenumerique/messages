import { AppLayout } from "./layout";
import { createContext, PropsWithChildren, useContext, useState } from "react";
import AuthenticatedView from "./authenticated-view";
import { MailboxProvider, useMailboxContext } from "@/features/providers/mailbox";
import { NoMailbox } from "./no-mailbox";
import { SentBoxProvider } from "@/features/providers/sent-box";
import { LeftPanel } from "./left-panel";
import { ModalStoreProvider } from "@/features/providers/modal-store";
import { useTheme } from "@/features/providers/theme";

export const MainLayout = ({ children }: PropsWithChildren) => {
    return (
        <AuthenticatedView>
            <MailboxProvider>
                <SentBoxProvider>
                    <ModalStoreProvider>
                        <MainLayoutContent>{children}</MainLayoutContent>
                    </ModalStoreProvider>
                </SentBoxProvider>
            </MailboxProvider>
        </AuthenticatedView>
    )
}

type LayoutContextType = {
    toggleLeftPanel: () => void;
    closeLeftPanel: () => void;
    openLeftPanel: () => void;
    isDragging: boolean;
    setIsDragging: (prevState: boolean) => void;
}

const LayoutContext = createContext<LayoutContextType | undefined>(undefined);

const MainLayoutContent = ({ children }: PropsWithChildren<{ simple?: boolean }>) => {
    const { mailboxes, queryStates } = useMailboxContext();
    const hasNoMailbox = queryStates.mailboxes.status === 'success' && mailboxes!.length === 0;
    const [leftPanelOpen, setLeftPanelOpen] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const { theme, variant } = useTheme();

    return (
        <LayoutContext.Provider value={{
            toggleLeftPanel: () => setLeftPanelOpen(!leftPanelOpen),
            closeLeftPanel: () => setLeftPanelOpen(false),
            openLeftPanel: () => setLeftPanelOpen(true),
            isDragging,
            setIsDragging,
        }}>
            <AppLayout
                enableResize
                isLeftPanelOpen={leftPanelOpen}
                setIsLeftPanelOpen={setLeftPanelOpen}
                leftPanelContent={<LeftPanel hasNoMailbox={hasNoMailbox} />}
                icon={<img src={`/images/${theme}/app-logo-${variant}.svg`} alt="logo" height={40} />}
                hideLeftPanelOnDesktop={hasNoMailbox}
                isDragging={isDragging}
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
    return context;
}

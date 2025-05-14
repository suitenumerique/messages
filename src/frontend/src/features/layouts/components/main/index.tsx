import { AppLayout } from "./layout";
import { Header, HeaderRight } from "../header";
import { MailboxPanel } from "@/features/layouts/components/mailbox-panel";
import { PropsWithChildren } from "react";
import { GlobalLayout } from "../global/global-layout";
import AuthenticatedView from "./authenticated-view";
import { MailboxProvider, useMailboxContext } from "@/features/providers/mailbox";
import { NoMailbox } from "./no-mailbox";
import { Toaster } from "@/features/ui/components/toaster";
import { SentBoxProvider } from "@/features/providers/sent-box";

export const MainLayout = ({ children }: PropsWithChildren) => {
    return (
        <GlobalLayout>
            <AuthenticatedView>
                <MailboxProvider>
                    <SentBoxProvider>
                        <MainLayoutContent>{children}</MainLayoutContent>
                        <Toaster />
                    </SentBoxProvider>
                </MailboxProvider>
            </AuthenticatedView>
        </GlobalLayout>
    )
}

const MainLayoutContent = ({ children }: PropsWithChildren) => {
    const { mailboxes, queryStates } = useMailboxContext();
    const hasNoMailbox = queryStates.mailboxes.status === 'success' && mailboxes!.length === 0;

    return hasNoMailbox ? (
        <>
            <Header />
            <NoMailbox />
        </>
    ) : (
        <>
            <AppLayout
                enableResize
                leftPanelContent={<MailboxPanel />}
                icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
                rightHeaderContent={<HeaderRight />}
            >
            {children}
        </AppLayout>
        </>
    )
}
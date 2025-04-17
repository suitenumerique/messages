import { MainLayout as KitMainLayout } from "@gouvfr-lasuite/ui-kit";
import { Header, HeaderRight } from "../header";
import { MailboxPanel } from "@/features/layouts/components/mailbox-panel";
import { PropsWithChildren } from "react";
import { GlobalLayout } from "../global/GlobalLayout";
import AuthenticatedView from "./authenticated-view";
import { MailboxProvider, useMailboxContext } from "@/features/mailbox/provider";
import { NoMailbox } from "./no-mailbox";

export const MainLayout = ({ children }: PropsWithChildren) => {
    return (
        <GlobalLayout>
            <AuthenticatedView>
                <MailboxProvider>
                    <MainLayoutContent>{children}</MainLayoutContent>
                </MailboxProvider>
            </AuthenticatedView>
        </GlobalLayout>
    )
}

const MainLayoutContent = ({ children }: PropsWithChildren) => {
    const { mailboxes, status } = useMailboxContext();
    const hasNoMailbox = status.mailboxes === 'success' && mailboxes.length === 0;

    return hasNoMailbox ? (
        <>
            <Header />
            <NoMailbox />
        </>
    ) : (
        <KitMainLayout
            enableResize
            leftPanelContent={<MailboxPanel />}
            icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
            rightHeaderContent={<HeaderRight />}
        >
            {children}
        </KitMainLayout>
    )
}
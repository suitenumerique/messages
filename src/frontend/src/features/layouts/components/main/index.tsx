import { MainLayout as KitMainLayout } from "@gouvfr-lasuite/ui-kit";
import { HeaderRight } from "../header";
import { MailboxPanel } from "@/features/layouts/components/mailbox-panel";
import { PropsWithChildren } from "react";
import { GlobalLayout } from "../global/GlobalLayout";

export const MainLayout = ({ children }: PropsWithChildren<{}>) => {
    return (
        <GlobalLayout>
            <KitMainLayout
                enableResize
                leftPanelContent={<MailboxPanel />}
                icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
                rightHeaderContent={<HeaderRight />}
            >
                {children}
            </KitMainLayout>
        </GlobalLayout>
    )
}
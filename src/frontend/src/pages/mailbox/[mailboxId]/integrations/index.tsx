import { MainLayout } from "@/features/layouts/components/main";
import { useEffect } from "react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { IntegrationsPageContent } from "@/features/layouts/components/mailbox-settings/integrations-view/page-content";
import { CreateIntegrationAction } from "@/features/layouts/components/mailbox-settings/integrations-view/create-integration-action";
import { useFeatureFlag, FEATURE_KEYS } from "@/hooks/use-feature";

const MailboxIntegrationsPage = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { queryStates, selectedMailbox } = useMailboxContext();
    const isIntegrationsEnabled = useFeatureFlag(FEATURE_KEYS.MAILBOX_ADMIN_CHANNELS);

    useEffect(() => {
        if (!queryStates.mailboxes.isLoading && !selectedMailbox) {
            router.push("/");
        }
    }, [queryStates.mailboxes.isLoading, selectedMailbox, router]);

    useEffect(() => {
        if (!isIntegrationsEnabled) {
            router.push("/");
        }
    }, [isIntegrationsEnabled, router]);

    if (!isIntegrationsEnabled) {
        return null;
    }

    return (
        <div className="admin-page">
            <div className="admin-page__header">
                <h1 className="title">{t("Integrations")}</h1>
                <div className="admin-page__actions">
                    <CreateIntegrationAction />
                </div>
            </div>

            <div className="admin-page__content">
                {selectedMailbox && <IntegrationsPageContent />}
            </div>
        </div>
    );
}

MailboxIntegrationsPage.getLayout = (page: React.ReactElement) => {
    return <MainLayout>{page}</MainLayout>;
};

export default MailboxIntegrationsPage;

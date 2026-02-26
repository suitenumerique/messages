import { MainLayout } from "@/features/layouts/components/main";
import { useEffect } from "react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { ManageAutorepliesViewPageContent } from "@/features/layouts/components/mailbox-settings/autoreplies-view/page-content";
import { ComposeAutoreplyAction } from "@/features/layouts/components/mailbox-settings/autoreplies-view/compose-autoreply-action";
import { Banner } from "@/features/ui/components/banner";

const MailboxAutorepliesPage = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { queryStates, selectedMailbox } = useMailboxContext();

    useEffect(() => {
        if (!queryStates.mailboxes.isLoading && !selectedMailbox) {
            router.push("/");
        }
    }, [queryStates.mailboxes.isLoading, selectedMailbox, router]);

    if (!selectedMailbox) return null;

    return (
        <div className="admin-page">
            <div className="admin-page__header">
                <h1 className="title">{t("Auto-replies for {{mailbox}}", { mailbox: selectedMailbox.email })}</h1>
                <div className="admin-page__actions">
                    <ComposeAutoreplyAction />
                </div>
            </div>

            <div className="admin-page__content">
                <div className="mb-sm mt-base">
                    <Banner type="info">
                        {t('Auto-replies are configured per mailbox. Only one auto-reply can be active at a time.', { mailbox: selectedMailbox.email })}
                    </Banner>
                </div>
                <ManageAutorepliesViewPageContent />
            </div>
        </div>
    );
}

MailboxAutorepliesPage.getLayout = (page: React.ReactElement) => {
    return <MainLayout>{page}</MainLayout>;
};

export default MailboxAutorepliesPage;

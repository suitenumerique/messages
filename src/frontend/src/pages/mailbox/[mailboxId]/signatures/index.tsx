import { MainLayout } from "@/features/layouts/components/main";
import { useEffect } from "react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { ManageSignaturesViewPageContent } from "@/features/layouts/components/mailbox-settings/signatures-view/page-content";
import { ComposeSignatureAction } from "@/features/layouts/components/mailbox-settings/signatures-view/compose-signature-action";
import { Banner } from "@/features/ui/components/banner";

const MailboxSignaturesPage = () => {
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
                <h1 className="title">{t("Signatures for {{mailbox}}", { mailbox: selectedMailbox.email })}</h1>
                <div className="admin-page__actions">
                    <ComposeSignatureAction />
                </div>
            </div>

            <div className="admin-page__content">
                <div className="mb-sm mt-base">
                    <Banner type="info">
                        {t('Those signatures are linked to the mailbox "{{mailbox}}". In case of a shared mailbox, all other mailbox users will be able to use them.', { mailbox: selectedMailbox.email })}
                    </Banner>
                </div>
                <ManageSignaturesViewPageContent />
            </div>
        </div>
    );
}

MailboxSignaturesPage.getLayout = (page: React.ReactElement) => {
    return <MainLayout>{page}</MainLayout>;
};

export default MailboxSignaturesPage;

import { MainLayout } from "@/features/layouts/components/main";
import { MessageForm } from "@/features/forms/components/message-form";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";

const NewMessageFormPage = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { queryStates, selectedMailbox } = useMailboxContext();

    /**
     * Go back to the previous page or to
     * the mailbox list if there is no previous page in the history
     */
    const handleClose = () => {
        if (window.history.length > 1) {
            router.back();
        } else if (!selectedMailbox) {
            router.push('/');
        } else {
            const defaultFolder = MAILBOX_FOLDERS[0];
            router.push(`/mailbox/${selectedMailbox.id}` + `?${new URLSearchParams(defaultFolder.filter).toString()}`);
        }
    }

    if (queryStates.mailboxes.isLoading) {
        return (
            <div className="thread-view thread-view--loading">
                <Spinner />
            </div>
        )
    }

    return (
        <div className="new-message-form-container">
            <h1>{t("new_message_form.title")}</h1>
            <MessageForm
                showSubject={true}
                onSuccess={handleClose}
                onClose={handleClose}
            />
        </div>
    );
};

NewMessageFormPage.getLayout = function getLayout(page: React.ReactElement) {
    return (
        <MainLayout>
            {page}
        </MainLayout>
    );
};

export default NewMessageFormPage;

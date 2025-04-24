import { MainLayout } from "@/features/layouts/components/main";
import { MessageForm } from "@/features/forms/components/message-form";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";

const NewMessageFormPage = () => {
    const { t } = useTranslation();
    const router = useRouter();

    /**
     * Go back to the previous page or to
     * the mailbox list if there is no previous page in the history
     */ 
    const handleClose = () => {
        if (window.history.length > 1) {
            router.back();
        } else {
            router.push('/');
        }
    }

    return (
        <div className="new-message-form-container">
            <h1>{t("new_message_form.title")}</h1>
            <MessageForm
                showSubject={true}
                onSuccess={() => router.push('/')}
                showMailboxes
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
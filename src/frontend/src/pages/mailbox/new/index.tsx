import { APIError } from "@/features/api/APIError";
import { useMessageCreateCreate } from "@/features/api/gen";
import { MainLayout } from "@/features/layouts/components/main";
import { useMailboxContext } from "@/features/mailbox/provider";
import soundbox from "@/features/utils/soundbox";
import { Alert, Button, Input, TextArea, VariantType } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

type NewMessageFormData = {
    to: string;
    cc?: string;
    bcc?: string;
    subject: string;
    body: string;
}

const NewMessageFormPage = () => {
    const { t } = useTranslation()
    const router = useRouter();
    const { mailboxes, invalidateThreadMessages } = useMailboxContext();
    const [error, setError] = useState<object | null>(null);
    const messageMutation = useMessageCreateCreate({
        mutation: {
            onSuccess: async () => {
                invalidateThreadMessages();
                await soundbox.play(0.35);
                router.replace(`/mailbox/${mailboxes![0].id}`);
            },
            onError: (error: APIError) => {
                setError(error.data);
            }
        }
    });
    const [showCCField, setShowCCField]  = useState(false);
    const [showBCCField, setShowBCCField]  = useState(false);

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = Object.fromEntries(new FormData(event.target as HTMLFormElement)) as NewMessageFormData;

        messageMutation.mutate({
            data: {
                to: formData.to.split(","),
                senderId: mailboxes![0].id,
                cc: formData.cc ? formData.cc.split(",") : undefined,
                bcc: formData.bcc ? formData.bcc.split(",") : undefined,
                subject: formData.subject,
                textBody: formData.body,
            },
        });
    }

    useEffect(() => {
        soundbox.load("/sounds/mail-sent.ogg");
    }, []);

    return (
        <div className="new-message-form-container">
            <h1>{t("new_message_form.title")}</h1>
            <form className="new-message-form" onSubmit={handleSubmit}>
                <div className="form-field-row">
                    <Input name="to" label={t("thread_message.to")} fullWidth required />
                    <Button type="button" size="nano" color="tertiary-text" onClick={() => setShowCCField(!showCCField)}>cc</Button>
                    <Button type="button" size="nano" color="tertiary-text" onClick={() => setShowBCCField(!showBCCField)}>bcc</Button>
                </div>
                {showCCField && (
                    <div className="form-field-row">
                        <Input name="cc" label={t("thread_message.cc")} fullWidth />
                    </div>
                )}
                {showBCCField && (
                    <div className="form-field-row">
                        <Input name="bcc" label={t("thread_message.bcc")} fullWidth />
                    </div>
                )}
                <div className="form-field-row">
                    <Input name="subject" label={t("thread_message.subject")} fullWidth required />
                </div>
                <div className="form-field-row">
                    <TextArea name="body" label={t("thread_message.body")} rows={10} fullWidth required />
                </div>
                {error && <Alert type={VariantType.ERROR} className="message-reply-form__error">{JSON.stringify(error)}</Alert>}
                <footer className="form-footer">
                    <Button>{t("actions.send")}</Button>
                    <Button type="button" color="secondary" onClick={() => router.back()}>{t("actions.cancel")}</Button>
                </footer>
            </form>
        </div>
    )
}

NewMessageFormPage.getLayout = function getLayout(page: React.ReactElement) {
    return (
        <MainLayout>
            {page}
        </MainLayout>
    )
}

export default NewMessageFormPage;
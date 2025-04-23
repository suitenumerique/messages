import { APIError } from "@/features/api/APIError";
import { Message, useMessageCreateCreate } from "@/features/api/gen";
import MessageEditor from "@/features/forms/components/message-editor";
import { useMailboxContext } from "@/features/mailbox/provider";
import soundbox from "@/features/utils/soundbox";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Alert, Button, Input, VariantType } from "@openfun/cunningham-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type MessageReplyFormProps = {
    handleClose: () => void
    replyAll: boolean
    message: Message
}

type ReplyFormData = {
    to: string
    cc: string | null
    bcc: string | null
    body: string
}

const MessageReplyForm = ({ handleClose, message, replyAll }: MessageReplyFormProps) => {
    const { t } = useTranslation();
    const formRef = useRef<HTMLFormElement>(null);
    const [error, setError] = useState<object | null>(null);
    const { selectedMailbox, invalidateThreadMessages } = useMailboxContext();
    const messageMutation = useMessageCreateCreate({
        mutation: {
            onSuccess: async () => {
                invalidateThreadMessages();
                await soundbox.play(0.35);
                handleClose();
            },
            onError: (error: APIError) => {
                setError(error.data);
            }
        }
    });

    const recipients = useMemo(() => {
        if (replyAll) {
            return [
                {email: message.sender.email},
                ...message.to,
                ...message.cc
            ].filter(contact => contact.email !== selectedMailbox!.email).map(contact => contact.email).join(',');
        }
        return message.sender.email;
    }, [message, replyAll]);

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = Object.fromEntries(new FormData(event.currentTarget)) as ReplyFormData;

        messageMutation.mutate({
            data: {
                parentId: message.id,
                senderId: selectedMailbox!.id,
                to: formData.to.split(','),
                subject: 'RE: ' + message!.subject,
                textBody: formData.body,
                htmlBody: formData.body,
                cc: formData.cc ? formData.cc.split(',') : undefined,
                bcc: formData.bcc ? formData.bcc.split(',') : undefined,
            }
        });
    }

    useEffect(() => {
        if (formRef.current) {
            const editor = formRef.current.querySelector(".message-editor .bn-editor") as HTMLDivElement;
            if (editor) {
                editor.focus();
            }
        }
        soundbox.load("/sounds/mail-sent.ogg");
    }, []);

    return (
        <form ref={formRef} className="message-reply-form" onSubmit={handleSubmit}>
            <Input name="to" required type="text" label={t('thread_message.to')} defaultValue={recipients} icon={<span className="material-icons">person</span>} fullWidth/>
            <Input name="cc" type="text" label={t('thread_message.cc')} icon={<span className="material-icons">person</span>} fullWidth/>
            <Input name="bcc" type="text" label={t('thread_message.bcc')} icon={<span className="material-icons">person</span>} fullWidth/>
            <MessageEditor name="body" />
            {error && <Alert type={VariantType.ERROR} className="message-reply-form__error">{JSON.stringify(error)}</Alert>}
            <footer className="message-reply-form__footer">
                    <Button
                        color="primary"
                        disabled={messageMutation.isPending}
                        type="submit"
                        icon={messageMutation.isPending ? <Spinner size="sm" /> : undefined}
                    >
                        {t("actions.send")}
                    </Button>
            <Button type="button" color="secondary" onClick={handleClose}>{t('actions.cancel')}</Button>
            </footer>
        </form>
    )
}

export default MessageReplyForm;
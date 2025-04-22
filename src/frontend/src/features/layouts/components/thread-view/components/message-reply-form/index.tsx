import { APIError } from "@/features/api/APIError";
import { Message, useMessageCreateCreate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/mailbox/provider";
import { Alert, Button, Input, TextArea, VariantType } from "@openfun/cunningham-react";
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
    message: string
}

const MessageReplyForm = ({ handleClose, message, replyAll }: MessageReplyFormProps) => {
    const { t } = useTranslation();
    const formRef = useRef<HTMLFormElement>(null);
    const [error, setError] = useState<object | null>(null);
    const { selectedMailbox, invalidateThreadMessages } = useMailboxContext();
    const mutation = useMessageCreateCreate({
        mutation: {
            onSuccess: () => {
                invalidateThreadMessages();
                handleClose();
            },
            onError: (error: APIError) => {
                console.log(error);
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

        mutation.mutate({
            data: {
                parentId: message.id,
                senderId: selectedMailbox!.id,
                to: formData.to.split(','),
                subject: 'RE: ' + message!.subject,
                textBody: formData.message,
                cc: formData.cc ? formData.cc.split(',') : undefined,
                bcc: formData.bcc ? formData.bcc.split(',') : undefined,
            }
        });
    }

    useEffect(() => {
        if (formRef.current) {
            const message = formRef.current.message;
            message.focus();
        }
    }, []);

    return (
        <form ref={formRef} className="message-reply-form" onSubmit={handleSubmit}>
            <Input name="to" required type="text" label={t('thread_message.to')} defaultValue={recipients} icon={<span className="material-icons">person</span>} fullWidth/>
            <Input name="cc" type="text" label={t('thread_message.cc')} icon={<span className="material-icons">person</span>} fullWidth/>
            <Input name="bcc" type="text" label={t('thread_message.bcc')} icon={<span className="material-icons">person</span>} fullWidth/>
            <TextArea required name="message" label="Message" fullWidth rows={10}/>
            {error && <Alert type={VariantType.ERROR} className="message-reply-form__error">{JSON.stringify(error)}</Alert>}
            <footer className="message-reply-form__footer">
            <Button type="button" color="secondary" onClick={handleClose}>{t('actions.cancel')}</Button>
            <Button type="submit">{t('actions.send')}</Button>
            </footer>
        </form>
    )
}

export default MessageReplyForm;
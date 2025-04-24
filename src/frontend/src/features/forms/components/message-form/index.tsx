import { APIError } from "@/features/api/APIError";
import { Message, useMessageCreateCreate } from "@/features/api/gen";
import MessageEditor from "@/features/forms/components/message-editor";
import { useMailboxContext } from "@/features/mailbox/provider";
import MailHelper from "@/features/utils/mail-helper";
import soundbox from "@/features/utils/soundbox";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Alert, Button, Input, Select, VariantType } from "@openfun/cunningham-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

enum MESSAGE_FORM_FIELDS {
    TO = "to",
    FROM = "from",
    CC = "cc",
    BCC = "bcc",
    SUBJECT = "subject",
    MESSAGE_EDITOR_HTML = "messageEditorHtml",
    MESSAGE_EDITOR_TEXT = "messageEditorText"
}

type MessageFormFields = {
    [key in MESSAGE_FORM_FIELDS]?: string;
}

interface MessageFormProps {
    // For reply mode
    parentMessage?: Message;
    replyAll?: boolean;
    onClose?: () => void;
    // For new message mode
    showSubject?: boolean;
    showMailboxes?: boolean;
    onSuccess?: () => void;
}

export const MessageForm = ({
    parentMessage,
    replyAll,
    onClose,
    showSubject = false,
    showMailboxes = false,
    onSuccess
}: MessageFormProps) => {
    const { t } = useTranslation();
    const [errors, setErrors] = useState<MessageFormFields | null>(null);
    const [showCCField, setShowCCField] = useState(false);
    const [showBCCField, setShowBCCField] = useState(false);
    const { selectedMailbox, mailboxes, invalidateThreadMessages } = useMailboxContext();
    const mustShowMailboxes = showMailboxes && mailboxes && mailboxes.length > 1;

    const nonFieldErrors = useMemo(() => {
        if (!errors) return null;

        const filteredErrors = Object.entries(errors).filter(([key]) => !Object.values(MESSAGE_FORM_FIELDS).includes(key as MESSAGE_FORM_FIELDS));
        if (filteredErrors.length === 0) return null;
        return filteredErrors.reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {} as Record<string, string>);
    }, [errors]);

    const getMailboxOptions = () => {
        if(!mailboxes) return [];
        return mailboxes.map((mailbox) => ({
            label: mailbox.email,
            value: mailbox.id
        }));
    }

    const messageMutation = useMessageCreateCreate({
        mutation: {
            onSettled: () => {
                setErrors(null);
            },
            onSuccess: async () => {
                await soundbox.play(0.07);
                invalidateThreadMessages();
                onSuccess?.();
                onClose?.();
            },
            onError: (error: APIError) => {
                setErrors(errors => ({ ...errors, ...error.data }));
            }
        }
    });

    const recipients = useMemo(() => {
        if (!parentMessage) return undefined;
        if (replyAll) {
            return [
                {email: parentMessage.sender.email},
                ...parentMessage.to,
                ...parentMessage.cc
            ]
                .filter(contact => contact.email !== selectedMailbox!.email)
                .map(contact => contact.email)
                .join(',');
        }
        return parentMessage.sender.email;
    }, [parentMessage, replyAll, selectedMailbox]);

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
        setErrors(null);
        event.preventDefault();
        const form = event.currentTarget;
        const formData = new FormData(form);
        const data = Object.fromEntries(formData) as MessageFormFields;
        let isValid = true;
        
        const to = MailHelper.parseRecipients(data.to!);
        const cc = data.cc ? MailHelper.parseRecipients(data.cc) : undefined;
        const bcc = data.bcc ? MailHelper.parseRecipients(data.bcc) : undefined;

        if (!data.from) {
            setErrors(errors => ({ ...errors, from: t("message_form.error.no_mailbox") }));
            isValid = false;
        }
        
        if (!MailHelper.areRecipientsValid(to)) {
            setErrors(errors => ({ ...errors, to: t("message_form.error.invalid_recipients") }));
            isValid = false;
        }

        if (!MailHelper.areRecipientsValid(cc, false)) {
            setErrors(errors => ({ ...errors, cc: t("message_form.error.invalid_recipients") }));
            isValid = false;
        }

        if (!MailHelper.areRecipientsValid(bcc, false)) {
            setErrors(errors => ({ ...errors, bcc: t("message_form.error.invalid_recipients") }));
            isValid = false;
        }

        if (!data.messageEditorHtml?.trim() || !data.messageEditorText?.trim()) {
            setErrors(errors => ({ ...errors, messageEditorHtml: t("message_form.error.messageEditorHtml") }));
            isValid = false;
        }

        if (!isValid) return;

        const subject = parentMessage 
            ? MailHelper.prefixSubjectIfNeeded(parentMessage.subject)
            : data.subject!.trim();

        messageMutation.mutate({
            data: {
                to,
                cc,
                bcc,
                subject,
                parentId: parentMessage?.id,
                htmlBody: data.messageEditorHtml,
                textBody: data.messageEditorText,
                senderId: data.from!,
            }
        });
    };

    useEffect(() => {
        soundbox.load("/sounds/mail-sent.ogg");
    }, []);

    useEffect(() => {
        if (!showCCField && errors?.cc) {
            setErrors(errors => ({ ...errors, cc: undefined }));
        }
    }, [showCCField])

    useEffect(() => {
        if (!showBCCField && errors?.bcc) {
            setErrors(errors => ({ ...errors, bcc: undefined }));
        }
    }, [showBCCField])

    return (
        <form className="message-form" onSubmit={handleSubmit}>
            <div className={clsx("form-field-row", {'form-field-row--hidden': !mustShowMailboxes})}>
                <Select
                    name="from"
                    options={getMailboxOptions()}
                    defaultValue={selectedMailbox?.id || mailboxes?.[0].id}
                    label={t("thread_message.from")}
                    clearable={false}
                    compact
                    fullWidth
                    showLabelWhenSelected={false}
                    disabled={!mustShowMailboxes}
                />
            </div>
            <div className="form-field-row">
                <Input 
                    name="to"
                    label={t("thread_message.to")} 
                    defaultValue={recipients}
                    icon={<span className="material-icons">group</span>}
                    text={errors?.to ?? t("message_form.helper_text.recipients")}
                    state={errors?.to ? "error" : "default"}
                    fullWidth
                    required
                />
                <Button type="button" size="nano" color={showCCField ? "tertiary" : "tertiary-text"} onClick={() => setShowCCField(!showCCField)}>cc</Button>
                <Button type="button" size="nano" color={showBCCField ? "tertiary" : "tertiary-text"} onClick={() => setShowBCCField(!showBCCField)}>bcc</Button>
            </div>

            {showCCField && (
                <div className="form-field-row">
                    <Input 
                        name="cc" 
                        label={t("thread_message.cc")} 
                        icon={<span className="material-icons">group</span>}
                        fullWidth
                        state={errors?.cc ? "error" : "default"}
                        text={errors?.cc ?? t("message_form.helper_text.recipients")}
                    />
                </div>
            )}

            {showBCCField && (
                <div className="form-field-row">
                    <Input 
                        name="bcc" 
                        label={t("thread_message.bcc")} 
                        icon={<span className="material-icons">visibility_off</span>}
                        fullWidth 
                        state={errors?.bcc ? "error" : "default"}
                        text={errors?.bcc ?? t("message_form.helper_text.recipients")}
                    />
                </div>
            )}

            {showSubject && (
                <div className="form-field-row">
                    <Input
                        name="subject"
                        label={t("thread_message.subject")}
                        fullWidth
                        required
                    />
                </div>
            )}

            <div className="form-field-row">
                <MessageEditor
                    blockNoteOptions={{ _tiptapOptions: { autofocus: true } }}
                    fullWidth
                    state={errors?.messageEditorHtml ? "error" : "default"}
                    text={errors?.messageEditorHtml}
                />
            </div>

            {nonFieldErrors && 
                <Alert type={VariantType.ERROR} className="message-form__error">
                    <ul>
                        {Object.entries(nonFieldErrors).map(([key, value]) => (
                            <li key={key}>{key}: {value}</li>
                        ))}
                    </ul>
                </Alert>
            }

            <footer className="form-footer">
                <Button
                    color="primary"
                    disabled={messageMutation.isPending}
                    type="submit"
                    icon={messageMutation.isPending ? <Spinner size="sm" /> : undefined}
                >
                    {t("actions.send")}
                </Button>
                <Button 
                    type="button" 
                    color="secondary" 
                    onClick={onClose}
                >
                    {t("actions.cancel")}
                </Button>
            </footer>
        </form>
    );
}; 
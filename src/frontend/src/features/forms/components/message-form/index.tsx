import { APIError } from "@/features/api/APIError";
import { Message, useDraftCreate, useDraftUpdate2, useSendCreate } from "@/features/api/gen";
import MessageEditor from "@/features/forms/components/message-editor";
import { useMailboxContext } from "@/features/mailbox/provider";
import useTrash from "@/features/message/useTrash";
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
    MESSAGE_EDITOR_TEXT = "messageEditorText",
    MESSAGE_EDITOR_DRAFT = "messageEditorDraft"
}

type MessageFormFields = {
    [key in MESSAGE_FORM_FIELDS]?: string;
}

interface MessageFormProps {
    // For reply mode
    draftMessage?: Message;
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
    draftMessage,
    onSuccess
}: MessageFormProps) => {
    const { t } = useTranslation();
    const [draft, setDraft] = useState<Message | undefined>(draftMessage);
    const [errors, setErrors] = useState<MessageFormFields | null>(null);
    const [showCCField, setShowCCField] = useState(false);
    const [showBCCField, setShowBCCField] = useState(false);
    const { markAsTrashed } = useTrash();
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

    const draftCreateMutation = useDraftCreate();
    const draftUpdateMutation = useDraftUpdate2();

    const messageMutation = useSendCreate({
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
        if (draft) return draft.to.map(contact => contact.email).join(',');
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


    /**
     * Validate the form input values according to the mode (draft or send).
     */
    const validateFormData = (form: HTMLFormElement, mode: 'draft' | 'send') => {
            setErrors(null);
            const formData = new FormData(form);
            const data = Object.fromEntries(formData) as MessageFormFields;
            let isValid = true;
            
            const to = MailHelper.parseRecipients(data.to!);
            const cc = data.cc ? MailHelper.parseRecipients(data.cc) : undefined;
            const bcc = data.bcc ? MailHelper.parseRecipients(data.bcc) : undefined;

            const subject = parentMessage 
                ? MailHelper.prefixSubjectIfNeeded(parentMessage.subject)
                : data.subject!.trim();

            if (mode === 'send') {
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
        
            } else if (mode === 'draft') {
                // In draft mode, at least a subject is required
                if (!data.from) {
                    setErrors(errors => ({ ...errors, from: t("message_form.error.no_mailbox") }));
                    isValid = false;
                }
                
                if (!MailHelper.areRecipientsValid(to)) {
                    setErrors(errors => ({ ...errors, to: t("message_form.error.invalid_recipients") }));
                    isValid = false;
                }
                
                if (!subject) {
                    setErrors(errors => ({ ...errors, subject: t("message_form.error.subject") }));
                    isValid = false;
                }
            }

            if (!isValid) return;

            return {
                to,
                cc,
                bcc,
                from: data.from!,
                subject,
                senderId: data.from!,
                parentId: parentMessage?.id,
                htmlBody: data.messageEditorHtml,
                textBody: data.messageEditorText,
                draftBody: data.messageEditorDraft,
            }
    
    }

    /**
     * Save the message as a draft when a form input is blurred.
     */
    const saveDraft = async (event: React.FormEvent<HTMLFormElement>) => {
        const data = validateFormData(event.currentTarget, 'draft');
        if (!data) return;
        const payload = {
            to: data.to,
            cc: data.cc,
            bcc: data.bcc,
            subject: data.subject,
            senderId: data.from!,
            parentId: parentMessage?.id,
            draftBody: data.draftBody,
        }
        let response;
        if (!draft) {
            response = await draftCreateMutation.mutateAsync({
                data: payload,
            });
        } else {
            response = await draftUpdateMutation.mutateAsync({
                messageId: draft.id,
                data: payload,
            });
        }

        setDraft(response.data as Message);
    }

    const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
        setErrors(null);
        event.preventDefault();
        const data = validateFormData(event.currentTarget, 'send');
        if (!data || !draft) return;

        messageMutation.mutate({
            data: {
                messageId: draft.id,
                htmlBody: data.htmlBody,
                textBody: data.textBody,
                senderId: data.from,
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
        <form className="message-form" onSubmit={handleSubmit} onBlur={saveDraft}>
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
                        defaultValue={draft?.cc?.map(contact => contact.email).join(',')}
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
                        defaultValue={draft?.bcc?.map(contact => contact.email).join(',')}
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
                        defaultValue={draft?.subject}
                        name="subject"
                        label={t("thread_message.subject")}
                        fullWidth
                        required
                    />
                </div>
            )}

            <div className="form-field-row">
                <MessageEditor
                    defaultValue={draftMessage?.draftBody}
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
                {onClose && (
                    <Button 
                        type="button" 
                        color="secondary" 
                        onClick={onClose}
                >
                        {t("actions.cancel")}
                    </Button>
                )}
                {
                    draftMessage && (
                        <Button 
                            type="button" 
                            color="secondary" 
                            onClick={() => markAsTrashed({ messageIds: [draftMessage.id] })}
                        >
                            {t("actions.delete_draft")}
                        </Button>
                    )
                }
            </footer>
        </form>
    );
}; 
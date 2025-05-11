import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Message, useDraftCreate, useDraftUpdate2, useSendCreate } from "@/features/api/gen";
import MessageEditor from "@/features/forms/components/message-editor";
import { useMailboxContext } from "@/features/mailbox/provider";
import useTrash from "@/features/message/use-trash";
import MailHelper from "@/features/utils/mail-helper";
import soundbox from "@/features/utils/soundbox";
import { RhfInput, RhfSelect } from "../react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";

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

// Zod schema for form validation
const toEmailArray = (value?: string) => {
    if (!value) return [];
    return value.split(',');
}
const emailArraySchema = z.array(z.string().trim().email("message_form.error.invalid_recipient"));
const messageFormSchema = z.object({
    from: z.string().nonempty("message_form.error.mailbox_required"),
    to: z.string()
         .min(1, "message_form.error.min_recipient")
         .transform(toEmailArray)
         .pipe(emailArraySchema),
    cc: z.string()
         .trim()
         .optional()
         .transform(toEmailArray)
        .pipe(emailArraySchema),
    bcc: z.string()
          .optional()
          .transform(toEmailArray)
          .pipe(emailArraySchema.optional()),
    subject: z.string()
        .trim()
        .nonempty("message_form.error.subject_required"),
    messageEditorHtml: z.string().optional().readonly(),
    messageEditorText: z.string().optional().readonly(),
    messageEditorDraft: z.string().optional().readonly(),
});

type MessageFormFields = z.infer<typeof messageFormSchema>;

const DRAFT_TOAST_ID = "MESSAGE_FORM_DRAFT_TOAST";

export const MessageForm = ({
    parentMessage,
    replyAll,
    onClose,
    draftMessage,
    onSuccess
}: MessageFormProps) => {
    const { t } = useTranslation();
    const [draft, setDraft] = useState<Message | undefined>(draftMessage);
    const [showCCField, setShowCCField] = useState((draftMessage?.cc?.length ?? 0) > 0);
    const [showBCCField, setShowBCCField] = useState((draftMessage?.bcc?.length ?? 0) > 0);
    const [pendingSubmit, setPendingSubmit] = useState(false);
    const { markAsTrashed } = useTrash();
    const { selectedMailbox, mailboxes, invalidateThreadMessages } = useMailboxContext();
    const hideSubjectField = Boolean(parentMessage);
    const hideFromField = (mailboxes?.length ?? 0) === 0 || draft;

    const getMailboxOptions = () => {
        if(!mailboxes) return [];
        return mailboxes.map((mailbox) => ({
            label: mailbox.email,
            value: mailbox.id
        }));
    }

    const recipients = useMemo(() => {
        if (draft) return draft.to.map(contact => contact.email);
        if (!parentMessage) return [];
        if (replyAll) {
            return [
                {email: parentMessage.sender.email},
                ...parentMessage.to,
                ...parentMessage.cc
            ]
                .filter(contact => contact.email !== selectedMailbox!.email)
                .map(contact => contact.email)
        }
        return [parentMessage.sender.email];
    }, [parentMessage, replyAll, selectedMailbox]);

    const formDefaultValues = useMemo(() => ({
        from: selectedMailbox?.id || mailboxes?.[0]?.id || '',
        to: (draft?.to?.map(contact => contact.email) ?? recipients).join(', '),
        cc: (draft?.cc?.map(contact => contact.email) ?? []).join(', '),
        bcc: (draft?.bcc?.map(contact => contact.email) ?? []).join(', '),
        subject: parentMessage ? 'RE' : (draft?.subject ?? ''),
        messageEditorDraft: draft?.draftBody,
        messageEditorHtml: undefined,
        messageEditorText: undefined,
    }), [draft, selectedMailbox])

    const form = useForm({
        resolver: zodResolver(messageFormSchema),
        mode: "onBlur",
        reValidateMode: "onBlur",
        shouldFocusError: false,
        defaultValues: formDefaultValues,
    });

    const messageMutation = useSendCreate({
        mutation: {
            onSettled: () => {
                form.clearErrors();
                setPendingSubmit(false);
                toast.dismiss(DRAFT_TOAST_ID);
            },
            onSuccess: async () => {
                await soundbox.play(0.07);
                invalidateThreadMessages();
                onSuccess?.();
                onClose?.();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("message_form.success.sent")}</span>
                    </ToasterItem>
                );
            }
        }
    });

    const handleDraftMutationSuccess = () => {
        addToast(
            <ToasterItem type="info">
                <span>{t("message_form.success.saved")}</span>
            </ToasterItem>,
            {
                toastId: DRAFT_TOAST_ID
            }
        );
    }

    const draftCreateMutation = useDraftCreate({
        mutation: { onSuccess: handleDraftMutationSuccess }
    });

    const draftUpdateMutation = useDraftUpdate2({
        mutation: { onSuccess: handleDraftMutationSuccess }
    });

    /**
     * Update or create a draft message if any field to change.
     */
    const saveDraft = async (data: MessageFormFields) => {
        if (Object.keys(form.formState.dirtyFields).length === 0) return draft;

        const subject = parentMessage 
            ? MailHelper.prefixSubjectIfNeeded(parentMessage.subject)
            : data.subject;

        const payload = {
            to: data.to,
            cc: data.cc || [],
            bcc: data.bcc || [],
            subject: subject,
            senderId: data.from!,
            parentId: parentMessage?.id,
            draftBody: data.messageEditorDraft,
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
        
        const newDraft = response.data as Message;
        setDraft(newDraft);
        return newDraft;
    }

    /**
     * Send the draft message
     */
    const handleSubmit = async (data: MessageFormFields) => {
        setPendingSubmit(true);

        // Ensure the draft is up to date before sending the message
        const draft = await saveDraft(data);

        if (!draft) { 
            setPendingSubmit(false);
            return;
        }

        messageMutation.mutate({
            data: {
                messageId: draft.id,
                senderId: data.from,
                htmlBody: data.messageEditorHtml,
                textBody: data.messageEditorText,
            }
        });
    };

    useEffect(() => {
        soundbox.load("/sounds/mail-sent.ogg");

        if (draftMessage) form.setFocus("subject");
        else form.setFocus("to")
    }, []);
    
    useEffect(() => {
        if (draft) {
            form.reset({
                ...formDefaultValues,
                messageEditorHtml: form.getValues('messageEditorHtml'),
                messageEditorText: form.getValues('messageEditorText'),
            }, { keepSubmitCount: true });
        }
    }, [draft]);

    useEffect(() => {
        if (!showCCField && form.formState.errors?.cc) {
            form.resetField("cc");
            form.clearErrors("cc");
        }
    }, [showCCField])

    useEffect(() => {
        if (!showBCCField && form.formState.errors?.bcc) {
            form.resetField("bcc");
            form.clearErrors("bcc");
        }
    }, [showBCCField])

    return (
        <FormProvider {...form}>
            <form className="message-form" onSubmit={form.handleSubmit(handleSubmit)} onBlur={form.handleSubmit(saveDraft)}>
                <div className={clsx("form-field-row", {'form-field-row--hidden': hideFromField})}>
                    <RhfSelect
                        name="from"
                        options={getMailboxOptions()}
                        label={t("thread_message.from")}
                        clearable={false}
                        compact
                        fullWidth
                        showLabelWhenSelected={false}
                        text={form.formState.errors.from && t(form.formState.errors.from.message as string)}
                    />
                </div>
                <div className="form-field-row">
                    <RhfInput 
                        name="to"
                        required
                        label={t("thread_message.to")} 
                        icon={<span className="material-icons">group</span>}
                        fullWidth
                        text={form.formState.errors.to && !Array.isArray(form.formState.errors.to) ? t(form.formState.errors.to.message as string) : t("message_form.helper_text.recipients")}
                        textItems={Array.isArray(form.formState.errors.to) ? form.formState.errors.to?.map((error, index) => t(error!.message as string, { email: form.getValues(`to`).split(',')[index] })) : undefined}
                    />
                    <Button tabIndex={-1} type="button" size="nano" color={showCCField ? "tertiary" : "tertiary-text"} onClick={() => setShowCCField(!showCCField)}>cc</Button>
                    <Button tabIndex={-1} type="button" size="nano" color={showBCCField ? "tertiary" : "tertiary-text"} onClick={() => setShowBCCField(!showBCCField)}>bcc</Button>
                </div>

                {showCCField && (
                    <div className="form-field-row">
                        <RhfInput 
                            name="cc"
                            label={t("thread_message.cc")} 
                            icon={<span className="material-icons">group</span>}
                            text={form.formState.errors.cc && !Array.isArray(form.formState.errors.cc) ? t(form.formState.errors.cc.message as string) : t("message_form.helper_text.recipients")}
                            textItems={Array.isArray(form.formState.errors.cc) ? form.formState.errors.cc?.map((error, index) => t(error!.message as string, { email: form.getValues('cc')?.split(',')[index] })) : []}
                            fullWidth
                        />
                    </div>
                )}

                {showBCCField && (
                    <div className="form-field-row">
                        <RhfInput
                            name="bcc"
                            label={t("thread_message.bcc")} 
                            icon={<span className="material-icons">visibility_off</span>}
                            text={form.formState.errors.bcc && !Array.isArray(form.formState.errors.bcc) ? t(form.formState.errors.bcc.message as string) : t("message_form.helper_text.recipients")}
                            textItems={Array.isArray(form.formState.errors.bcc) ? form.formState.errors.bcc?.map((error, index) => t(error!.message as string, { email: form.getValues('bcc')?.split(',')[index] })) : []}
                            fullWidth 
                        />
                    </div>
                )}

                <div className={clsx("form-field-row", {'form-field-row--hidden': hideSubjectField})}>
                        <RhfInput
                            name="subject"
                            label={t("thread_message.subject")}
                            text={form.formState.errors.subject && t(form.formState.errors.subject.message as string)}
                            fullWidth
                        />
                    </div>

                <div className="form-field-row">
                    <MessageEditor
                        defaultValue={form.getValues('messageEditorDraft')}
                        fullWidth
                        state={form.formState.errors?.messageEditorDraft ? "error" : "default"}
                        text={form.formState.errors?.messageEditorDraft?.message}
                    />
                </div>

                <footer className="form-footer">
                    <Button
                        color="primary"
                        disabled={pendingSubmit}
                        icon={pendingSubmit ? <Spinner size="sm" /> : undefined}
                        type="submit"
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
        </FormProvider>
    );
}; 
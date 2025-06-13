import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Alert,Button, VariantType } from "@openfun/cunningham-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { DraftMessageRequestRequest, Message, sendCreateResponse200, useDraftCreate, useDraftUpdate2, useMessagesDestroy, useSendCreate } from "@/features/api/gen";
import MessageEditor from "@/features/forms/components/message-editor";
import { useMailboxContext } from "@/features/providers/mailbox";
import MailHelper from "@/features/utils/mail-helper";
import { RhfInput, RhfSelect } from "../react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";
import { useSentBox } from "@/features/providers/sent-box";
import { useRouter } from "next/router";
import { AttachmentUploader } from "./attachment-uploader";
import { useAlbert } from "@/hooks/use-albert";

interface MessageFormProps {
    // For reply mode
    draftMessage?: Message;
    parentMessage?: Message;
    replyAll?: boolean;
    onClose?: () => void;
    // For new message mode
    showSubject?: boolean;
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
         .optional()
         .transform(toEmailArray)
         .pipe(emailArraySchema),
    cc: z.string()
         .optional()
         .transform(toEmailArray)
         .pipe(emailArraySchema),
    bcc: z.string()
          .optional()
          .transform(toEmailArray)
          .pipe(emailArraySchema),
    subject: z.string()
        .trim()
        .nonempty("message_form.error.subject_required"),
    messageEditorHtml: z.string().optional().readonly(),
    messageEditorText: z.string().optional().readonly(),
    messageEditorDraft: z.string().optional().readonly(),
    attachments: z.array(z.object({
        blobId: z.string(),
        name: z.string(),
    })).optional(),
    driveAttachments: z.array(z.object({
        id: z.string(),
        name: z.string(),
        url: z.string(),
        type: z.string(),
        size: z.number(),
        created_at: z.string(),
    })).optional(),
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
    const router = useRouter();
    const { checkMissingAttachments } = useAlbert();
    const [hasMissingAttachments, setHasMissingAttachments] = useState(false);
    const [draft, setDraft] = useState<Message | undefined>(draftMessage);
    const [showCCField, setShowCCField] = useState((draftMessage?.cc?.length ?? 0) > 0);
    const [showBCCField, setShowBCCField] = useState((draftMessage?.bcc?.length ?? 0) > 0);
    const [pendingSubmit, setPendingSubmit] = useState(false);
    const { selectedMailbox, mailboxes, invalidateThreadMessages, invalidateThreadsStats, unselectThread } = useMailboxContext();
    const hideSubjectField = Boolean(parentMessage);
    const defaultSenderId = mailboxes?.find((mailbox) => {
        if (draft?.sender) return draft.sender.email === mailbox.email;
        return selectedMailbox?.id === mailbox.id;
    })?.id ?? mailboxes?.[0]?.id;
    const hideFromField = defaultSenderId && (mailboxes?.length ?? 0) === 1;
    const { addQueuedMessage } = useSentBox();

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
            return [...new Set([
                {email: parentMessage.sender.email},
                ...parentMessage.to,
                ...parentMessage.cc
                ]
                .filter(contact => contact.email !== selectedMailbox!.email)
                .map(contact => contact.email)
            )]
        }
        // If the sender is replying to himself, we can consider that it prefers
        // to reply to the message recipient from onw of its message.
        if (parentMessage.sender.email === selectedMailbox?.email) {
            if (parentMessage.to.length > 0) {
                return parentMessage.to.map(contact => contact.email);
            }
            if (parentMessage.cc.length > 0) {
                return parentMessage.cc.map(contact => contact.email);
            }
            if (parentMessage.bcc.length > 0) {
                return parentMessage.bcc.map(contact => contact.email);
            }
        }
        return [parentMessage.sender.email];
    }, [parentMessage, replyAll, selectedMailbox]);

    const formDefaultValues = useMemo(() => ({
        from: defaultSenderId ?? '',
        to: (draft?.to?.map(contact => contact.email) ?? recipients).join(', '),
        cc: (draft?.cc?.map(contact => contact.email) ?? []).join(', '),
        bcc: (draft?.bcc?.map(contact => contact.email) ?? []).join(', '),
        subject: parentMessage ? 'RE' : (draft?.subject ?? ''),
        messageEditorDraft: MailHelper.extractDriveAttachmentsFromDraft(draft?.draftBody)[0],
        messageEditorHtml: undefined,
        messageEditorText: undefined,
        attachments: draft?.attachments.map(a => ({ blobId: a.blobId, name: a.name })),
        driveAttachments: MailHelper.extractDriveAttachmentsFromDraft(draft?.draftBody)[1],
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
            onSuccess: async (response) => {
                const taskId = (response as sendCreateResponse200).data.task_id;
                addQueuedMessage(taskId);
                onSuccess?.();
                onClose?.();
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
        mutation: { onSuccess: () => {
            invalidateThreadsStats();
            handleDraftMutationSuccess();
        }}
    });

    const draftUpdateMutation = useDraftUpdate2({
        mutation: { onSuccess: handleDraftMutationSuccess }
    });

    const deleteMessageMutation = useMessagesDestroy();

    const handleDeleteMessage = (messageId: string) => {
        if(window.confirm(t("message_form.confirm.delete"))) {
            deleteMessageMutation.mutate({
                id: messageId
            }, {
                onSuccess: () => {
                    setDraft(undefined);
                    invalidateThreadMessages();
                    invalidateThreadsStats();
                    unselectThread();
                    addToast(
                        <ToasterItem type="info">
                            <span>{t("message_form.success.draft_deleted")}</span>
                        </ToasterItem>
                    );
                    onClose?.();
                },
            });
        }
    }

    /**
     * If the user changes the message sender, we need to delete the draft,
     * then recreate a new one. Once the new draft is created, we need to
     * redirect the user to the new draft view.
     */
    const handleChangeSender = async (data: DraftMessageRequestRequest) => {
        if (draft && form.formState.dirtyFields.from) {
            await deleteMessageMutation.mutateAsync({ id: draft.id });
            const response = await draftCreateMutation.mutateAsync({ data }, {
                onSuccess: () => {addToast(
                    <ToasterItem type="info">
                        <span>{t("message_form.success.draft_transferred")}</span>
                    </ToasterItem>,
                );
                }
            });

            if(router.asPath.includes("new")) {
                setDraft(response.data as Message);
                return;
            }
            const mailboxId = data.senderId;
            const threadId = response.data.thread_id
            // @TODO: Make something less hardcoded to improve the maintainability of the code
            router.replace(`/mailbox/${mailboxId}/thread/${threadId}?has_draft=1`);
        }
    }

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
            senderId: data.from,
            parentId: parentMessage?.id,
            draftBody: MailHelper.attachDriveAttachmentsToDraft(data.messageEditorDraft, form.getValues('driveAttachments')),
            attachments: form.getValues('attachments'),
        }
        let response;
        
        if (!draft) {
            response = await draftCreateMutation.mutateAsync({
                data: payload,
            });
        } else if (form.formState.dirtyFields.from) {
            handleChangeSender(payload);
            return;
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

        // recipients are optional to save the draft but required to send the message
        // so we have to manually check that at least one recipient is present.
        const hasNoRecipients = data.to.length === 0 && data.cc.length === 0 && data.bcc.length === 0;
        if (hasNoRecipients) {
            setPendingSubmit(false);
            form.setError("to", { message: t("message_form.error.min_recipient") });
            return;
        }

        const draft = await saveDraft(data);

        if (!draft) { 
            setPendingSubmit(false);
            return;
        }

        messageMutation.mutate({
            data: {
                messageId: draft.id,
                senderId: data.from,
                htmlBody: MailHelper.attachDriveAttachmentsToHtmlBody(form.getValues('messageEditorHtml'), form.getValues('driveAttachments')),
                textBody: MailHelper.attachDriveAttachmentsToTextBody(form.getValues('messageEditorText'), form.getValues('driveAttachments')),
            }
        });
    };

    const checkAttachments = async () => {
        const driveAttachments = form.getValues('driveAttachments') || [];
        const attachments = form.getValues('attachments') || [];
        const hasAttachments = [...driveAttachments, ...attachments].length > 0;
        const content = form.getValues('messageEditorHtml');
        if (!content || content === '<div data-id="react-email-markdown"></div>' || hasAttachments) {
            setHasMissingAttachments(false);
            return;
        }

        const containMissingAttachments = await checkMissingAttachments(content);

        if (containMissingAttachments && !hasAttachments) {
            setHasMissingAttachments(true);
        }
        else if (!containMissingAttachments || hasAttachments) {
            setHasMissingAttachments(false);
        }
    }

    /**
     * Prevent the Enter key press to trigger onClick on input children (like file input)
     */
    const handleKeyDown = (event: React.KeyboardEvent) => {
        if (event.key === 'Enter') {
            event.preventDefault();
        }
    }

    useEffect(() => {
        if (draftMessage) form.setFocus("subject");
        else form.setFocus("to")
    }, []);

    useEffect(() => {
        if (draft) {
            form.reset(undefined, { keepSubmitCount: true, keepDirty: false, keepValues: true, keepDefaultValues: false });
            checkAttachments();
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
            <form
                className="message-form"
                onSubmit={form.handleSubmit(handleSubmit)}
                onBlur={form.handleSubmit(saveDraft)}
                onKeyDown={handleKeyDown}
            >
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
                        label={t("thread_message.to")} 
                        icon={<span className="material-icons">group</span>}
                        fullWidth
                        text={form.formState.errors.to && !Array.isArray(form.formState.errors.to) ? t(form.formState.errors.to.message as string) : t("message_form.helper_text.recipients")}
                        textItems={Array.isArray(form.formState.errors.to) ? form.formState.errors.to?.map((error, index) => t(error!.message as string, { email: form.getValues(`to`)!.split(',')[index] })) : undefined}
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

                {
                    hasMissingAttachments === true && (
                        <div className="form-field-row">
                            <div className="alert alert--warning">
                                <p>{t('message_form.error.missing_attachments')}</p>
                            </div>
                        </div>
                    )
                }

                <AttachmentUploader initialAttachments={[...(draft?.attachments ?? []), ...(form.getValues('driveAttachments') ?? [])]} onChange={form.handleSubmit(saveDraft)} />

                <footer className="form-footer">
                    <Button
                        color="primary"
                        disabled={!draft || pendingSubmit}
                        icon={pendingSubmit ? <Spinner size="sm" /> : undefined}
                        type="submit"
                    >
                        {t("actions.send")}
                    </Button>
                    {!draft && onClose && (
                        <Button 
                            type="button" 
                            color="secondary" 
                            onClick={onClose}
                    >
                            {t("actions.cancel")}
                        </Button>
                    )}
                    {
                        draft && (
                            <Button 
                                type="button" 
                                color="secondary" 
                                onClick={() => handleDeleteMessage(draft.id)}
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

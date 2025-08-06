import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState, useRef } from "react";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import z from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Attachment, DraftMessageRequestRequest, Message, sendCreateResponse200, useDraftCreate, useDraftUpdate2, useMessagesDestroy, useSendCreate } from "@/features/api/gen";
import MessageEditor from "@/features/forms/components/message-editor";
import { useMailboxContext } from "@/features/providers/mailbox";
import MailHelper from "@/features/utils/mail-helper";
import { RhfInput, RhfSelect } from "../react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";
import { useSentBox } from "@/features/providers/sent-box";
import { useRouter } from "next/router";
import { AttachmentUploader } from "./attachment-uploader";
import { DateHelper } from "@/features/utils/date-helper";
import { Banner } from "@/features/ui/components/banner";
import { RhfContactComboBox } from "../react-hook-form/rhf-contact-combobox";
import { DriveFile } from "./drive-attachment-picker";
import useAbility, { Abilities } from "@/hooks/use-ability";

export type MessageFormMode = "new" |"reply" | "reply_all" | "forward";

interface MessageFormProps {
    // For reply mode
    draftMessage?: Message;
    parentMessage?: Message;
    mode?: MessageFormMode;
    onClose?: () => void;
    // For new message mode
    showSubject?: boolean;
    onSuccess?: () => void;
}

// Zod schema for form validation
const emailArraySchema = z.array(z.email({ error: "message_form.error.invalid_recipient" }));
const attachmentSchema = z.object({
    blobId: z.uuid(),
    name: z.string(),
});
const driveAttachmentSchema = z.object({
    id: z.string(),
    name: z.string(),
    url: z.url(),
    type: z.string(),
    size: z.number(),
    created_at: z.string(),
});
const messageFormSchema = z.object({
    from: z.string().nonempty({ error: "message_form.error.mailbox_required" }),
    to: emailArraySchema,
    cc: emailArraySchema.optional(),
    bcc: emailArraySchema.optional(),
    subject: z.string().trim(),
    messageEditorHtml: z.string().optional().readonly(),
    messageEditorText: z.string().optional().readonly(),
    messageEditorDraft: z.string().optional().readonly(),
    attachments: z.array(attachmentSchema).optional(),
    driveAttachments: z.array(driveAttachmentSchema).optional(),
});

type MessageFormFields = z.infer<typeof messageFormSchema>;

const DRAFT_TOAST_ID = "MESSAGE_FORM_DRAFT_TOAST";

export const MessageForm = ({
    parentMessage,
    mode = "new",
    onClose,
    draftMessage,
    onSuccess
}: MessageFormProps) => {
    const { t } = useTranslation();
    const router = useRouter();
    const [draft, setDraft] = useState<Message | undefined>(draftMessage);
    const [showCCField, setShowCCField] = useState((draftMessage?.cc?.length ?? 0) > 0);
    const [showBCCField, setShowBCCField] = useState((draftMessage?.bcc?.length ?? 0) > 0);
    const [pendingSubmit, setPendingSubmit] = useState(false);
    const [currentTime, setCurrentTime] = useState(new Date());
    const autoSaveTimerRef = useRef<NodeJS.Timeout | null>(null);
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
        if (!mode.startsWith("reply") || !parentMessage) return [];

        if (mode === "reply_all") {
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
        // to reply to the message recipient.
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
    }, [parentMessage, mode, selectedMailbox]);

    const getDefaultSubject = () => {
        if (draft?.subject) return draft.subject
        if (parentMessage) {
            if (mode === "forward") return MailHelper.prefixSubjectIfNeeded(parentMessage.subject ?? "", "Fwd:");
            if (mode.startsWith("reply")) return MailHelper.prefixSubjectIfNeeded(parentMessage.subject ?? "", "Re:");
        }

        return '';
    }

    const getDefaultAttachments = () => {
        let attachments: Attachment[] = [];
        if (draft?.attachments) attachments = [...draft.attachments];
        if (mode === "forward" && parentMessage?.attachments) attachments = [...parentMessage.attachments];
        return attachments;
    }

    const formDefaultValues = useMemo(() => {
        const [draftBody, draftDriveAttachments] = MailHelper.extractDriveAttachmentsFromDraft(draft?.draftBody ?? '');
        return {
            from: defaultSenderId ?? '',
            to: draft?.to?.map(contact => contact.email) ?? recipients,
            cc: draft?.cc?.map(contact => contact.email) ?? [],
            bcc: draft?.bcc?.map(contact => contact.email) ?? [],
            subject: getDefaultSubject(),
            messageEditorDraft: draftBody,
            messageEditorHtml: undefined,
            messageEditorText: undefined,
            attachments: getDefaultAttachments(),
            driveAttachments: draftDriveAttachments,
        }
    }, [draft, selectedMailbox])

    const form = useForm({
        resolver: zodResolver(messageFormSchema),
        mode: "onBlur",
        reValidateMode: "onBlur",
        shouldFocusError: false,
        defaultValues: formDefaultValues,
    });

    const messageEditorDraft = useWatch({
        control: form.control,
        name: "messageEditorDraft",
    }) || "";

    const attachments = useWatch({
        control: form.control,
        name: "attachments",
    }) || [];

    const driveAttachments = useWatch({
        control: form.control,
        name: "driveAttachments",
    }) || [];

    const currentSenderId = useWatch({
        control: form.control,
        name: "from",
    });
    const currentSender = mailboxes?.find((mailbox) => mailbox.id === currentSenderId);
    const canSendMessages = useAbility(Abilities.CAN_SEND_MESSAGES, currentSender!);
    const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, currentSender!);
    const canChangeSender = !draft || canWriteMessages;

    const initialAttachments = useMemo((): (Attachment | DriveFile)[] => {
        return [...(draft?.attachments ?? []), ...(driveAttachments ?? [])];
    }, [draft, driveAttachments]);

    const showAttachmentsForgetAlert = useMemo(() => {
        return MailHelper.areAttachmentsMentionedInDraft(messageEditorDraft) && attachments.length === 0 && driveAttachments.length === 0;
    }, [messageEditorDraft, attachments, driveAttachments]);

    const messageMutation = useSendCreate({
        mutation: {
            onSettled: () => {
                form.clearErrors();
                setPendingSubmit(false);
                toast.dismiss(DRAFT_TOAST_ID);
            },
            onSuccess: async (response) => {
                const data = (response as sendCreateResponse200).data
                const taskId = data.task_id;
                addQueuedMessage(taskId);
                onSuccess?.();
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
            stopAutoSave();
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
        if (!canWriteMessages) return;
        const canSaveDraft = (
            Object.keys(form.formState.dirtyFields).length > 0
            && (
                draft || (
                    data.subject.length > 0
                    || data.to.length > 0
                    || (data.cc?.length ?? 0) > 0
                    || (data.bcc?.length ?? 0) > 0
                    || (data.messageEditorText?.length ?? 0) > 0
                    || (data.attachments?.length ?? 0) > 0
                    || (data.driveAttachments?.length ?? 0) > 0
                )
            )
        )
        if (!canSaveDraft) return draft;

        const payload = {
            to: data.to,
            cc: data.cc ?? [],
            bcc: data.bcc ?? [],
            subject: data.subject,
            senderId: data.from,
            parentId: parentMessage?.id,
            draftBody: MailHelper.attachDriveAttachmentsToDraft(data.messageEditorDraft, data.driveAttachments),
            attachments: data.attachments,
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
     * Auto-save draft every 30 seconds
     */
    const startAutoSave = () => {
        // Clear existing timer
        if (autoSaveTimerRef.current) {
            clearInterval(autoSaveTimerRef.current);
        }

        // Start new timer
        autoSaveTimerRef.current = setInterval(() => {
            form.handleSubmit(saveDraft)();
        }, 30000); // 30 seconds
    };

    const stopAutoSave = () => {
        if (autoSaveTimerRef.current) {
            clearInterval(autoSaveTimerRef.current);
            autoSaveTimerRef.current = null;
        }
    };

    /**
     * Send the draft message
     */
    const handleSubmit = async (data: MessageFormFields) => {
        if (!canSendMessages) return;
        setPendingSubmit(true);
        stopAutoSave(); // Stop auto-save when submitting

        // recipients are optional to save the draft but required to send the message
        // so we have to manually check that at least one recipient is present.
        const hasNoRecipients = data.to.length === 0 && (data.cc?.length ?? 0) === 0 && (data.bcc?.length ?? 0) === 0;
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
                htmlBody: MailHelper.attachDriveAttachmentsToHtmlBody(data.messageEditorHtml, data.driveAttachments),
                textBody: MailHelper.attachDriveAttachmentsToTextBody(data.messageEditorText, data.driveAttachments),
            }
        });
    };

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
        }
    }, [draft]);

    // Start auto-save when component mounts
    useEffect(() => {
        startAutoSave();

        // Cleanup on unmount
        return () => {
            stopAutoSave();
        };
    }, []);

    // Restart auto-save when form becomes dirty
    useEffect(() => {
        if (Object.keys(form.formState.dirtyFields).length > 0) {
            startAutoSave();
        }
    }, [form.formState.dirtyFields]);

    // Update current time every 15 seconds for relative time display
    useEffect(() => {
        const timeUpdateInterval = setInterval(() => {
            setCurrentTime(new Date());
        }, 15000); // 15 seconds

        return () => {
            clearInterval(timeUpdateInterval);
        };
    }, []);

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
                        disabled={!canChangeSender}
                        compact
                        fullWidth
                        showLabelWhenSelected={false}
                        text={form.formState.errors.from && t(form.formState.errors.from.message as string)}
                    />
                </div>
                <div className="form-field-row">
                    <RhfContactComboBox
                        name="to"
                        label={t("thread_message.to")}
                        // icon={<span className="material-icons">group</span>}
                        text={form.formState.errors.to && !Array.isArray(form.formState.errors.to) ? t(form.formState.errors.to.message as string) : t("message_form.helper_text.recipients")}
                        textItems={Array.isArray(form.formState.errors.to) ? form.formState.errors.to?.map((error, index) => t(error!.message as string, { email: form.getValues('to')?.[index] })) : []}
                        disabled={!canWriteMessages}
                        fullWidth
                        clearable
                    />
                    <Button tabIndex={-1} type="button" size="nano" color={showCCField ? "tertiary" : "tertiary-text"} onClick={() => setShowCCField(!showCCField)}>cc</Button>
                    <Button tabIndex={-1} type="button" size="nano" color={showBCCField ? "tertiary" : "tertiary-text"} onClick={() => setShowBCCField(!showBCCField)}>bcc</Button>
                </div>

                {showCCField && (
                    <div className="form-field-row">
                        <RhfContactComboBox
                            name="cc"
                            label={t("thread_message.cc")}
                            // icon={<span className="material-icons">group</span>}
                            text={form.formState.errors.cc && !Array.isArray(form.formState.errors.cc) ? t(form.formState.errors.cc.message as string) : t("message_form.helper_text.recipients")}
                            textItems={Array.isArray(form.formState.errors.cc) ? form.formState.errors.cc?.map((error, index) => t(error!.message as string, { email: form.getValues('cc')?.[index] })) : []}
                            disabled={!canWriteMessages}
                            fullWidth
                            clearable
                        />
                    </div>
                )}

                {showBCCField && (
                    <div className="form-field-row">
                        <RhfContactComboBox
                            name="bcc"
                            label={t("thread_message.bcc")}
                            // icon={<span className="material-icons">visibility_off</span>}
                            text={form.formState.errors.bcc && !Array.isArray(form.formState.errors.bcc) ? t(form.formState.errors.bcc.message as string) : t("message_form.helper_text.recipients")}
                            textItems={Array.isArray(form.formState.errors.bcc) ? form.formState.errors.bcc?.map((error, index) => t(error!.message as string, { email: form.getValues('bcc')?.[index] })) : []}
                            disabled={!canWriteMessages}
                            fullWidth
                            clearable
                        />
                    </div>
                )}

                <div className={clsx("form-field-row", {'form-field-row--hidden': hideSubjectField})}>
                        <RhfInput
                            name="subject"
                            label={t("thread_message.subject")}
                            text={form.formState.errors.subject && t(form.formState.errors.subject.message as string)}
                            disabled={!canWriteMessages}
                            fullWidth
                        />
                    </div>

                <div className="form-field-row">
                    <MessageEditor
                        defaultValue={form.getValues('messageEditorDraft')}
                        fullWidth
                        state={form.formState.errors?.messageEditorDraft ? "error" : "default"}
                        text={form.formState.errors?.messageEditorDraft?.message}
                        quotedMessage={mode !== "new" ? parentMessage : undefined}
                        disabled={!canWriteMessages}
                    />
                </div>

                <AttachmentUploader
                    initialAttachments={initialAttachments}
                    onChange={form.handleSubmit(saveDraft)}
                    disabled={!canWriteMessages}
                />

                {showAttachmentsForgetAlert &&
                  <Banner type="warning">
                    {t("attachments.forgot_question")}
                  </Banner>
                }

                <div className="form-field-row form-field-save-time">
                    {
                        (draftCreateMutation.isPending || draftUpdateMutation.isPending) && (
                            <Spinner size="sm" />
                        )
                    }
                    {
                        draft && (
                            t("message_form.last_save.label", { relativeTime: t(...DateHelper.formatRelativeTime(draft.updated_at, currentTime)) })
                        )
                    }
                </div>
                <footer className="form-footer">
                    <Button
                        color="primary"
                        disabled={!canSendMessages || !draft || pendingSubmit}
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
                        canWriteMessages && draft && (
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

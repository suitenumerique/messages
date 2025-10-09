import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState, useRef } from "react";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import z from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Attachment, DraftMessageRequestRequest, Message, sendCreateResponse200, useDraftCreate, useDraftUpdate2, useMessagesDestroy, useSendCreate } from "@/features/api/gen";
import { MessageComposer, QuoteType } from "@/features/forms/components/message-composer";
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
import i18n from "@/features/i18n/initI18n";
import { DropdownButton } from "@/features/ui/components/dropdown-button";
import { PREFER_SEND_MODE_KEY, PreferSendMode } from "@/features/config/constants";

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
const emailArraySchema = z.array(z.email({ error: i18n.t("The email {{email}} is invalid.") }));
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
    from: z.string().nonempty({ error: i18n.t("Mailbox is required.") }),
    to: emailArraySchema,
    cc: emailArraySchema.optional(),
    bcc: emailArraySchema.optional(),
    subject: z.string().trim(),
    messageHtmlBody: z.string().optional().readonly(),
    messageTextBody: z.string().optional().readonly(),
    messageDraftBody: z.string().optional().readonly(),
    attachments: z.array(attachmentSchema).optional(),
    driveAttachments: z.array(driveAttachmentSchema).optional(),
    signatureId: z.string().optional().nullable(),
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
    const [preferredSendMode, setPreferredSendMode] = useState<PreferSendMode>(() => {
        if (mode === 'new') return PreferSendMode.SEND;
        return localStorage.getItem(PREFER_SEND_MODE_KEY) as PreferSendMode ?? PreferSendMode.SEND;
    });
    const [showCCField, setShowCCField] = useState((draftMessage?.cc?.length ?? 0) > 0);
    const [showBCCField, setShowBCCField] = useState((draftMessage?.bcc?.length ?? 0) > 0);
    const [pendingSubmit, setPendingSubmit] = useState<{ archive: boolean } | null>(null);
    const [currentTime, setCurrentTime] = useState(new Date());
    const autoSaveTimerRef = useRef<NodeJS.Timeout | null>(null);
    const quoteType: QuoteType | undefined = mode !== "new" ? (mode === "forward" ? "forward" : "reply") : undefined;
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
            messageDraftBody: draftBody,
            messageHtmlBody: undefined,
            messageTextBody: undefined,
            attachments: getDefaultAttachments(),
            driveAttachments: draftDriveAttachments,
            signatureId: draft?.signature?.id,
        }
    }, [draft, selectedMailbox])

    const form = useForm({
        resolver: zodResolver(messageFormSchema),
        mode: "onBlur",
        reValidateMode: "onBlur",
        shouldFocusError: false,
        defaultValues: formDefaultValues,
    });

    const messageDraftBody = useWatch({
        control: form.control,
        name: "messageDraftBody",
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
        return MailHelper.areAttachmentsMentionedInDraft(messageDraftBody) && attachments.length === 0 && driveAttachments.length === 0;
    }, [messageDraftBody, attachments, driveAttachments]);

    const messageMutation = useSendCreate({
        mutation: {
            onSettled: () => {
                form.clearErrors();
                toast.dismiss(DRAFT_TOAST_ID);
            },
            onSuccess: async (response, { data: variables }) => {
                const data = (response as sendCreateResponse200).data;
                const taskId = data.task_id;
                const shouldCloseThread = !!variables.archive;
                addQueuedMessage(taskId, shouldCloseThread);
                onSuccess?.();
            }
        }
    });

    const handleDraftMutationSuccess = () => {
        addToast(
            <ToasterItem type="info">
                <span>{t("Draft saved")}</span>
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
    const isSavingDraft = draftCreateMutation.isPending || draftUpdateMutation.isPending || deleteMessageMutation.isPending;
    const isSubmittingMessage = pendingSubmit !== null || messageMutation.isPending;

    const handleDeleteMessage = (messageId: string) => {
        if(window.confirm(t("Are you sure you want to delete this draft? This action cannot be undone."))) {
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
                            <span>{t("Draft deleted")}</span>
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
                        <span>{t("Draft transferred to another mailbox")}</span>
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
     * Update or create a draft message if any field to change.
     */
    const saveDraft = async (data: MessageFormFields) => {
        if (!canWriteMessages || isSavingDraft) return;
        stopAutoSave();

        const saveDraftNeeded = (
            Object.keys(form.formState.dirtyFields).length > 0
            && (
                !!draft || (
                    data.subject.length > 0
                    || data.to.length > 0
                    || (data.cc?.length ?? 0) > 0
                    || (data.bcc?.length ?? 0) > 0
                    || (data.messageTextBody?.length ?? 0) > 0
                    || (data.attachments?.length ?? 0) > 0
                    || (data.driveAttachments?.length ?? 0) > 0
                    || (data.signatureId?.length ?? 0) > 0
                )
            )
        )

        if (!saveDraftNeeded) {
            return;
        }

        const payload = {
            to: data.to,
            cc: data.cc ?? [],
            bcc: data.bcc ?? [],
            subject: data.subject,
            senderId: data.from,
            parentId: parentMessage?.id,
            draftBody: MailHelper.attachDriveAttachmentsToDraft(data.messageDraftBody, data.driveAttachments),
            attachments: data.attachments,
            signatureId: data.signatureId ?? null,
        }

        let response;

        try {
            if (!draft) {
                response = await draftCreateMutation.mutateAsync({
                    data: payload,
                });
            } else if (form.formState.dirtyFields.from) {
                await handleChangeSender(payload);
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
        } catch (error) {
            console.error("Error in saveDraft:", error);
        } finally {
            startAutoSave();
        }
    }

    /**
     * Send the draft message
     */
    const handleSubmit = async (data: MessageFormFields, { archive }: { archive: boolean }) => {
        if (!canSendMessages) return;

        // recipients are optional to save the draft but required to send the message
        // so we have to manually check that at least one recipient is present.
        const hasNoRecipients = data.to.length === 0 && (data.cc?.length ?? 0) === 0 && (data.bcc?.length ?? 0) === 0;
        if (hasNoRecipients) {
            form.setError("to", { message: t("At least one recipient is required.") });
            return;
        }
        stopAutoSave(); // Stop auto-save when submitting

        if (isSavingDraft) {
            // Do not trigger the submit but mark the form as pending for submitting
            setPendingSubmit({ archive });
            return;
        }
        setPendingSubmit(null);

        // Only save if there are unsaved changes, otherwise use existing draft
        let draftToSend = draft;
        if (Object.keys(form.formState.dirtyFields).length > 0 || !draft) {
            draftToSend = await saveDraft(data);
        }

        if (!draftToSend) {
            return;
        }

        messageMutation.mutate({
            data: {
                messageId: draftToSend.id,
                senderId: data.from,
                htmlBody: MailHelper.attachDriveAttachmentsToHtmlBody(data.messageHtmlBody, data.driveAttachments),
                textBody: MailHelper.attachDriveAttachmentsToTextBody(data.messageTextBody, data.driveAttachments),
                archive,
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
        return stopAutoSave;
    }, []);

    // Effect to retriger handleSubmit if the form is pending for submit
    // and the draft save state is updated to false
    useEffect(() => {
        if (pendingSubmit !== null && !isSavingDraft) {
            handleSubmit(form.getValues(), pendingSubmit);
        }
    }, [isSavingDraft]);

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

    useEffect(() => {
        localStorage.setItem(PREFER_SEND_MODE_KEY, preferredSendMode);
    }, [preferredSendMode])

    return (
        <FormProvider {...form}>
            <form
                className="message-form"
                onSubmit={form.handleSubmit(data => handleSubmit(data, { archive: preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE }))}
                onBlur={form.handleSubmit(saveDraft)}
                onKeyDown={handleKeyDown}
            >
                <div className={clsx("form-field-row", {'form-field-row--hidden': hideFromField})}>
                    <RhfSelect
                        name="from"
                        options={getMailboxOptions()}
                        label={t("From: ")}
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
                        label={t("To:")}
                        // icon={<span className="material-icons">group</span>}
                        text={form.formState.errors.to && !Array.isArray(form.formState.errors.to) ? form.formState.errors.to.message: t("Enter the email addresses of the recipients separated by commas")}
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
                            label={t("Copy: ")}
                            // icon={<span className="material-icons">group</span>}
                            text={form.formState.errors.cc && !Array.isArray(form.formState.errors.cc) ? t(form.formState.errors.cc.message as string) : t("Enter the email addresses of the recipients separated by commas")}
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
                            label={t("Blind copy: ")}
                            // icon={<span className="material-icons">visibility_off</span>}
                            text={form.formState.errors.bcc && !Array.isArray(form.formState.errors.bcc) ? t(form.formState.errors.bcc.message as string) : t("Enter the email addresses of the recipients separated by commas")}
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
                            label={t("Subject: ")}
                            text={form.formState.errors.subject && form.formState.errors.subject.message}
                            disabled={!canWriteMessages}
                            fullWidth
                        />
                    </div>

                <div className="form-field-row">
                    <MessageComposer
                        mailboxId={form.getValues('from')}
                        defaultValue={form.getValues('messageDraftBody')}
                        fullWidth
                        state={form.formState.errors?.messageDraftBody ? "error" : "default"}
                        text={form.formState.errors?.messageDraftBody?.message}
                        quotedMessage={quoteType ? parentMessage : undefined}
                        quoteType={quoteType}
                        disabled={!canWriteMessages}
                        draft={draft}
                        submitDraft={form.handleSubmit(saveDraft)}
                    />
                </div>

                <AttachmentUploader
                    initialAttachments={initialAttachments}
                    onChange={form.handleSubmit(saveDraft)}
                    disabled={!canWriteMessages}
                />

                {showAttachmentsForgetAlert &&
                  <Banner type="warning">
                    {t("Did you forget an attachment?")}
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
                            t("Last saved {{relativeTime}}", { relativeTime: DateHelper.formatRelativeTime(draft.updated_at, currentTime) })
                        )
                    }
                </div>
                <footer className="form-footer">
                    <DropdownButton
                        color="primary"
                        disabled={!canSendMessages || isSubmittingMessage}
                        type="submit"
                        dropdownOptions={[
                            ...(mode !== 'new' ? [{
                                label: preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE ? t("Send") : t("Send and archive"),
                                icon: <Icon name={preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE ? "send" : "send_and_archive"} type={IconType.OUTLINED} />,
                                callback: form.handleSubmit(data => handleSubmit(data, { archive: preferredSendMode !== PreferSendMode.SEND_AND_ARCHIVE })),
                                showSeparator: true,
                            }, {
                                label: t("Use \"Send and archive\" by default"),
                                icon: <Icon name={preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE ? "check_box" : "check_box_outline_blank"} type={IconType.OUTLINED} />,
                                callback: () => setPreferredSendMode(preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE ? PreferSendMode.SEND : PreferSendMode.SEND_AND_ARCHIVE)
                            }] : [])
                        ]}
                    >
                        {preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE && t("Send and archive")}
                        {preferredSendMode === PreferSendMode.SEND && t("Send")}
                    </DropdownButton>
                    {!draft && onClose && (
                        <Tooltip content={t("Delete")}>
                            <Button
                                type="button"
                                color="tertiary"
                                onClick={onClose}
                                aria-label={t("Delete")}
                                icon={<Icon name="delete" type={IconType.OUTLINED} />}
                            />
                        </Tooltip>
                    )}
                    {
                        canWriteMessages && draft && (
                            <Tooltip content={t("Delete draft")}>
                                <Button
                                    type="button"
                                    color="tertiary"
                                    onClick={() => handleDeleteMessage(draft.id)}
                                    aria-label={t("Delete draft")}
                                    icon={<Icon name="delete" type={IconType.OUTLINED} />}
                                />
                            </Tooltip>
                        )
                    }
                </footer>
            </form>
        </FormProvider>
    );
};

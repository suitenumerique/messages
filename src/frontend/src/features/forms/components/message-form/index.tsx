import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Tooltip, useModals } from "@gouvfr-lasuite/cunningham-react";
import { clsx } from "clsx";
import { useEffect, useMemo, useState, useRef, forwardRef, useImperativeHandle } from "react";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import z from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Attachment, DraftMessageRequestRequest, draftCreateResponse200, Message, sendCreateResponse200, useDraftCreate, useDraftUpdate2, useMessagesDestroy, useSendCreate, ThreadAccessRoleChoices } from "@/features/api/gen";
import { MessageComposer, MessageComposerHandle, QuoteType } from "@/features/forms/components/message-composer";
import { useMailboxContext } from "@/features/providers/mailbox";
import MailHelper from "@/features/utils/mail-helper";
import { RhfInput, RhfSelect } from "../react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";
import { useSentBox } from "@/features/providers/sent-box";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { AttachmentUploader } from "./attachment-uploader";
import { DateHelper } from "@/features/utils/date-helper";
import { Banner } from "@/features/ui/components/banner";
import { RhfContactComboBox } from "../react-hook-form/rhf-contact-combobox";
import useAbility, { Abilities } from "@/hooks/use-ability";
import i18n from "@/features/i18n/initI18n";
import { DropdownButton } from "@/features/ui/components/dropdown-button";
import { PREFER_SEND_MODE_KEY, PreferSendMode } from "@/features/config/constants";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useConfig } from "@/features/providers/config";
import { DriveFile } from "./drive-attachment-picker";
import { useAttachments } from "@/features/forms/hooks/use-attachments";
import { MessageComposerHelper } from "@/features/utils/composer-helper";

export type MessageFormMode = "new" | "reply" | "reply_all" | "forward";

interface MessageFormProps {
    // For reply mode
    draftMessage?: Message;
    parentMessage?: Message;
    mode?: MessageFormMode;
    onClose?: () => void;
    // For new message mode
    showSubject?: boolean;
    onSuccess?: () => void;
    // Notifies the parent whether the current user may delete this draft, so a
    // surrounding surface (e.g. the draft header menu) can mirror the same
    // mailbox + thread permission check used to gate the in-form delete button.
    onDeletableChange?: (deletable: boolean) => void;
}

// Zod schema for form validation
const emailArraySchema = z.array(z.email({ error: i18n.t("The email {{email}} is invalid.") }));
const attachmentSchema = z.object({
    blobId: z.string(), // Can be UUID or msg_{messageId}_{index} format
    name: z.string(),
    cid: z.string().optional().nullable(),
    size: z.number().optional(), // Size in bytes, present for inline images
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
    messageDraftBody: z.string().optional().readonly(),
    attachments: z.array(attachmentSchema).optional(),
    driveAttachments: z.array(driveAttachmentSchema).optional(),
    signatureId: z.string().optional().nullable(),
});

export type MessageFormValues = z.infer<typeof messageFormSchema>;

// Imperative handle so a parent (e.g. the draft compose surface header) can
// trigger the form's coordinated draft deletion instead of duplicating its
// autosave-aware logic.
export type MessageFormHandle = {
    deleteDraft: () => void;
};

const DRAFT_TOAST_ID = "MESSAGE_FORM_DRAFT_TOAST";

export const MessageForm = forwardRef<MessageFormHandle, MessageFormProps>(({
    parentMessage,
    mode = "new",
    onClose,
    draftMessage,
    onSuccess,
    onDeletableChange
}, ref) => {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const location = useLocation();
    const searchParams = useUrlSearchParams();
    const config = useConfig();
    const modals = useModals();
    const composerRef = useRef<MessageComposerHandle>(null);
    const [draft, setDraftState] = useState<Message | undefined>(draftMessage);
    const {
      selectedMailbox,
      selectedThread,
      mailboxes,
      removeMessages,
      patchMessages,
      invalidateMailbox,
      invalidateThreadList,
      invalidateThreadsStats,
      unselectThread,
      unpinThreads,
      pinThreads
    } = useMailboxContext();
    // Synchronous mirror of `draft`. `saveDraftInner` may be re-entered (via
    // composer onChange firing right after `ensureDraft` resolves) before
    // React commits the previous setDraft, so the closure-captured `draft`
    // would be stale and we'd POST a second time instead of PATCH.
    const draftRef = useRef<Message | undefined>(draftMessage);
    const setDraft = (next: Message | undefined) => {
        draftRef.current = next;
        setDraftState(next);
    };
    const [preferredSendMode, setPreferredSendMode] = useState<PreferSendMode>(() => {
        if (mode === 'new') return PreferSendMode.SEND;
        return localStorage.getItem(PREFER_SEND_MODE_KEY) as PreferSendMode ?? PreferSendMode.SEND;
    });
    const saveDraftPromiseRef = useRef<Promise<string | undefined> | null>(null);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [currentTime, setCurrentTime] = useState(new Date());
    const autoSaveTimerRef = useRef<NodeJS.Timeout | null>(null);
    const saveDraftRef = useRef<() => void>(() => {});
    const quoteType: QuoteType | undefined = mode !== "new" ? (mode === "forward" ? "forward" : "reply") : undefined;
    const hideSubjectField = Boolean(draftMessage?.parent_id ?? parentMessage);
    // For replies/forwards, only allow sending from a mailbox that has access to the thread.
    const availableMailboxes = useMemo(() => {
        if (!mailboxes) return [];
        if (mode === "new" || !selectedThread) return mailboxes;
        const allowedMailboxIds = new Set(selectedThread.accesses.filter(access => access.role === ThreadAccessRoleChoices.editor).map((access) => access.mailbox.id));
        return mailboxes.filter((mailbox) => allowedMailboxIds.has(mailbox.id));
    }, [mailboxes, mode, selectedThread]);
    const defaultSenderId = availableMailboxes.find((mailbox) => {
        if (draft?.sender) return draft.sender.email === mailbox.email;
        return selectedMailbox?.id === mailbox.id;
    })?.id ?? availableMailboxes[0]?.id;
    // Effective sender used by reply prefills: defaultSenderId can diverge from
    // selectedMailbox after the availableMailboxes filter, so recipient filters
    // and the "reply to self" branch must align on the chosen sender.
    const replySenderEmail =
        draft?.sender?.email ??
        availableMailboxes.find((mailbox) => mailbox.id === defaultSenderId)?.email ??
        selectedMailbox?.email;
    const hideFromField = defaultSenderId && availableMailboxes.length === 1;
    const { addQueuedMessage } = useSentBox();

    const getMailboxOptions = () => {
        return availableMailboxes.map((mailbox) => ({
            label: mailbox.email,
            value: mailbox.id
        }));
    }

    const toRecipients = useMemo(() => {
        if (draft) return draft.to.map(({ contact }) => contact.email);
        if (!mode.startsWith("reply") || !parentMessage) return [];

        if (mode === "reply_all") {
            return [...new Set([
                { contact: { email: parentMessage.sender.email } },
                ...parentMessage.to
            ]
                .filter(({ contact }) => contact.email !== replySenderEmail)
                .map(({ contact }) => contact.email)
            )]
        }
        // If the sender is replying to himself, we can consider that it prefers
        // to reply to the message recipient.
        if (parentMessage.sender.email === replySenderEmail) {
            if (parentMessage.to.length > 0) {
                return parentMessage.to.map(({ contact }) => contact.email);
            }
            if (parentMessage.cc.length > 0) {
                return parentMessage.cc.map(({ contact }) => contact.email);
            }
            if (parentMessage.bcc.length > 0) {
                return parentMessage.bcc.map(({ contact }) => contact.email);
            }
        }
        return [parentMessage.sender.email];
    }, [parentMessage, mode, replySenderEmail]);

    const ccRecipients = useMemo(() => {
        if (draft) return draft.cc.map(({ contact }) => contact.email);
        if (mode === "reply_all" && parentMessage) {
            return parentMessage.cc
                .filter(({ contact }) => contact.email !== replySenderEmail)
                .map(({ contact }) => contact.email);
        }
        return [];
    }, [parentMessage, mode, draft, replySenderEmail]);

    const [showCCField, setShowCCField] = useState(ccRecipients.length > 0);
    const [showBCCField, setShowBCCField] = useState((draftMessage?.bcc?.length ?? 0) > 0);

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
            to: toRecipients,
            cc: ccRecipients,
            bcc: draft?.bcc?.map(({ contact }) => contact.email) ?? [],
            subject: getDefaultSubject(),
            messageDraftBody: draftBody,
            attachments: getDefaultAttachments(),
            driveAttachments: draftDriveAttachments,
            signatureId: draft?.signature?.id,
        }
    }, [draft, defaultSenderId, toRecipients, ccRecipients, parentMessage, mode])

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

    const currentToRecipients = useWatch({
        control: form.control,
        name: "to",
    }) || [];

    const currentCcRecipients = useWatch({
        control: form.control,
        name: "cc",
    }) || [];

    const currentBccRecipients = useWatch({
        control: form.control,
        name: "bcc",
    }) || [];

    const currentSenderId = useWatch({
        control: form.control,
        name: "from",
    });
    const currentSender = mailboxes?.find((mailbox) => mailbox.id === currentSenderId);
    const canEditCurrentThread = useAbility(Abilities.CAN_EDIT_THREAD, selectedThread);
    const canEditThread = mode === "new" ? true : canEditCurrentThread;
    const canSendMessages = useAbility(Abilities.CAN_SEND_MESSAGES, currentSender!) && canEditThread;
    const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, currentSender!) && canEditThread;
    const canChangeSender = !draft || canWriteMessages;
    // Same gate as the in-form delete button below: requires write rights on the
    // sender mailbox AND edit rights on the thread, and an existing draft.
    const canDeleteDraft = canWriteMessages && !!draft;

    const initialAttachments = useMemo((): (Attachment | DriveFile)[] => {
        // Include parent message attachments when forwarding
        const forwardedAttachments = (mode === "forward" && !draft && parentMessage?.attachments)
            ? parentMessage.attachments
            : [];
        const [, draftDriveAttachments] = MailHelper.extractDriveAttachmentsFromDraft(draft?.draftBody ?? '');
        return [...(draft?.attachments ?? []), ...forwardedAttachments, ...(draftDriveAttachments ?? [])];
    }, [draft, mode, parentMessage]);

    const attachmentHook = useAttachments({
        mailboxId: currentSenderId,
        initialAttachments,
        form,
        onChange: () => saveDraftRef.current(),
        maxAttachmentSize: config.MAX_OUTGOING_ATTACHMENT_SIZE,
    });

    const showAttachmentsForgetAlert = useMemo(() => {
        return MailHelper.areAttachmentsMentionedInDraft(messageDraftBody) && attachmentHook.attachments.length === 0;
    }, [messageDraftBody, attachmentHook.attachments]);

    const totalRecipients = useMemo(() => {
        return (currentToRecipients?.length || 0) + (currentCcRecipients?.length || 0) + (currentBccRecipients?.length || 0);
    }, [currentToRecipients, currentCcRecipients, currentBccRecipients]);

    const showRecipientLimitWarning = useMemo(() => {
        const maxRecipients = config.MAX_RECIPIENTS_PER_MESSAGE;
        return maxRecipients > 0 && totalRecipients > maxRecipients;
    }, [config.MAX_RECIPIENTS_PER_MESSAGE, totalRecipients]);

    const messageMutation = useSendCreate({
        mutation: {
            onError: () => startAutoSave(),
            onSettled: () => {
                setIsSubmitting(false);
                form.clearErrors();
                toast.dismiss(DRAFT_TOAST_ID);
            },
            onSuccess: async (response, variables) => {
                const data = (response as sendCreateResponse200).data;
                // The backend un-drafts the message synchronously before queuing
                // the SMTP task. Reflect that optimistically so the thread renders
                // it as a sending message right away instead of keeping the draft
                // form open until the background refetch lands.
                const sentThreadId = draft?.thread_id ?? selectedThread?.id;
                if (sentThreadId) {
                    patchMessages(sentThreadId, (message) =>
                        message.id === variables.data.messageId
                            ? { ...message, is_draft: false }
                            : message
                    );
                }
                addQueuedMessage(data.task_id);
                onSuccess?.();
            }
        }
    });

    // Only surfaced on draft creation. Subsequent updates are intentionally
    // silent: the in-form "saved at" label already conveys the save state, and
    // a toast flashing on every autosave is more distracting than informative.
    const notifyDraftCreated = () => {
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
        mutation: {
            onSuccess: (response) => {
                const message = (response as draftCreateResponse200).data;
                // Patch + pin so a thread that just acquired a draft stays
                // accurate even when filtered out of the next refetch (e.g.
                // marked-as-read while viewing "unread"): mergePinnedThreads
                // would otherwise re-insert the cached version with
                // `has_draft: false` and the drafts filter would miss it.
                if (message.thread_id) {
                    pinThreads([message.thread_id], (thread) => ({
                        ...thread,
                        has_draft: true,
                        draft_messaged_at: message.created_at,
                    }));
                }
                invalidateMailbox();
                invalidateThreadsStats();
                notifyDraftCreated();
            }
        }
    });

    const draftUpdateMutation = useDraftUpdate2();


    const deleteMessageMutation = useMessagesDestroy();
    const isDeletingDraft = deleteMessageMutation.isPending;
    const isSubmittingMessage = isSubmitting || messageMutation.isPending;

    const handleDeleteMessage = async (messageId: string) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t("Delete draft")}</span>,
            children: t("Are you sure you want to delete this draft? This action cannot be undone."),
        });
        if (decision !== 'delete') return;

        await saveDraftPromiseRef.current;
        stopAutoSave();

        const isSingleMessage = selectedThread?.messages.length === 1;
        deleteMessageMutation.mutate({
            id: messageId
        }, {
            onSuccess: () => {
                onClose?.();
                setDraft(undefined);
                if (selectedThread) {
                    removeMessages(selectedThread.id, [messageId]);
                    // The thread may exit the active filter (e.g. drafts) once
                    // its only draft is gone. Drop any pin so the next refetch
                    // is authoritative.
                    unpinThreads([selectedThread.id]);
                }
                if (isSingleMessage) {
                    invalidateThreadList();
                    unselectThread();
                } else {
                    invalidateMailbox();
                    // Unselect the thread if we are in the draft view
                    if (searchParams.get('has_draft') === '1') {
                        unselectThread();
                    }
                }
                invalidateThreadsStats();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Draft deleted")}</span>
                    </ToasterItem>
                );
            },
            onError: startAutoSave,
        });
    }

    useImperativeHandle(ref, () => ({
        deleteDraft: () => {
            const id = draftRef.current?.id;
            if (id) void handleDeleteMessage(id);
        },
    }));

    useEffect(() => {
        onDeletableChange?.(canDeleteDraft);
    }, [canDeleteDraft, onDeletableChange]);

    /**
     * If the user changes the message sender, we need to delete the draft,
     * then recreate a new one. Once the new draft is created, we need to
     * redirect the user to the new draft view.
     */
    const handleChangeSender = async (data: DraftMessageRequestRequest) => {
        if (draft && form.formState.dirtyFields.from) {
            await deleteMessageMutation.mutateAsync({ id: draft.id });
            const response = await draftCreateMutation.mutateAsync({ data }, {
                onSuccess: () => {
                    addToast(
                        <ToasterItem type="info">
                            <span>{t("Draft transferred to another mailbox")}</span>
                        </ToasterItem>,
                    );
                }
            });

            if (location.pathname.includes("new")) {
                setDraft(response.data as Message);
                return;
            }
            const mailboxId = data.senderId;
            const threadId = (response.data as Message).thread_id
            if (threadId) {
                navigate({ to: '/mailbox/$mailboxId/thread/$threadId', params: { mailboxId, threadId }, search: { has_draft: '1' }, replace: true });
            }
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
     * Whether the form holds genuine user content worth persisting as a draft.
     * Recipients alone do not trigger creation by design: only a subject, body
     * or attachment does.
     *
     * Everything is measured against the initial template (`formDefaultValues`),
     * not against zero, so reply/forward behave like a new message: the prefilled
     * "Re:"/"Fwd:" subject and the attachments carried over from a forwarded
     * message do not, on their own, create a draft. The body is checked through
     * the editor blocks (not its raw length), and `hasUserBodyContent` ignores
     * the auto-inserted signature and quoted-message blocks — so a pristine
     * composer counts as empty in every mode.
     */
    const hasUserDraftContent = (data: MessageFormValues): boolean => {
        const subjectChanged =
            data.subject.trim() !== (formDefaultValues.subject ?? "").trim();
        const initialAttachmentCount =
            (formDefaultValues.attachments?.length ?? 0) +
            (formDefaultValues.driveAttachments?.length ?? 0);
        const currentAttachmentCount =
            (data.attachments?.length ?? 0) + (data.driveAttachments?.length ?? 0);
        return (
            subjectChanged
            || currentAttachmentCount > initialAttachmentCount
            || MessageComposerHelper.hasUserBodyContent(data.messageDraftBody)
        )
    }

    /**
     * Update or create a draft message if any field to change.
     * When `force` is true, bypass the content check (used by ensureDraft).
     * Returns the draft id on success.
     */
    const saveDraftInner = async (force = false): Promise<string | undefined> => {
        if (saveDraftPromiseRef.current) return saveDraftPromiseRef.current;

        const data = form.getValues();
        if (!canWriteMessages) return draft?.id;

        // Once a draft exists, keep the reactive behavior (save on any dirty
        // field, plus the 30s timer). Before a draft exists, only create one
        // when the user has actually entered content: relying on `dirtyFields`
        // here is both unreliable (it lags behind the synchronous form values,
        // hence the "save only on the second blur" bug) and too eager (the
        // composer marks `messageDraftBody` dirty on mount with an empty doc).
        const saveDraftNeeded = force || (
            draftRef.current
                ? Object.keys(form.formState.dirtyFields).length > 0
                : hasUserDraftContent(data)
        )

        if (!saveDraftNeeded) {
            return draft?.id;
        }

        const payload = {
            to: data.to,
            cc: data.cc ?? [],
            bcc: data.bcc ?? [],
            subject: data.subject,
            senderId: data.from,
            parentId: parentMessage?.id ?? draft?.parent_id,
            draftBody: MailHelper.attachDriveAttachmentsToDraft(data.messageDraftBody, data.driveAttachments),
            attachments: data.attachments,
            signatureId: data.signatureId ?? null,
        }

        const promise = (async () => {
            let response;
            try {
                stopAutoSave();
                const isDirtyFrom = !!form.formState.dirtyFields.from;
                form.reset(form.getValues(), { keepSubmitCount: true, keepDirty: false, keepValues: true, keepDefaultValues: false });
                // Read the latest draft from the ref rather than the state:
                // a previous saveDraftInner call may have just created the
                // draft without React having committed setDraft yet.
                const currentDraft = draftRef.current;
                if (!currentDraft) {
                    response = await draftCreateMutation.mutateAsync({
                        data: payload,
                    });
                } else if (isDirtyFrom) {
                    await handleChangeSender(payload);
                    return currentDraft.id;
                } else {
                    response = await draftUpdateMutation.mutateAsync({
                        messageId: currentDraft.id,
                        data: payload,
                    });
                }

                const newDraft = response.data as Message;
                setDraft(newDraft);
                return newDraft.id;
            } catch (error) {
                console.warn("Error in saveDraft:", error);
                return draftRef.current?.id;
            } finally {
                saveDraftPromiseRef.current = null;
                // Only resume the periodic auto-save once a draft actually
                // exists; an empty new-message form must not be polled.
                if (draftRef.current) startAutoSave();
            }
        })();

        saveDraftPromiseRef.current = promise;
        return promise;
    }

    const saveDraft = () => saveDraftInner(false);

    /**
     * Ensure a draft exists, creating one if necessary.
     * Returns the draft id.
     */
    const ensureDraft = async (): Promise<string | undefined> => {
        if (draft) return draft.id;
        return saveDraftInner(true);
    }

    saveDraftRef.current = form.handleSubmit(saveDraft);

    /**
     * Send the draft message
     */
    const handleSubmit = async ({ archive }: { archive: boolean }) => {
        const data = form.getValues();

        // recipients are optional to save the draft but required to send the message
        // so we have to manually check that at least one recipient is present.
        const hasNoRecipients = data.to.length === 0 && (data.cc?.length ?? 0) === 0 && (data.bcc?.length ?? 0) === 0;
        if (hasNoRecipients) {
            form.setError("to", { message: t("At least one recipient is required.") });
            return;
        }
        if (!canSendMessages || !composerRef.current) return;

        setIsSubmitting(true);

        try {
            // Wait for any in-progress draft save to complete
            await saveDraftPromiseRef.current;

            // Ensure a draft exists before sending (creates one on-the-fly if needed)
            const messageId = draft?.id ?? await ensureDraft();
            if (!messageId) {
                setIsSubmitting(false);
                return;
            }

            // Generate HTML and text body from the editor at send time to avoid
            // calling blocksToMarkdownLossy on every keystroke (which creates real
            // <img> DOM elements and triggers unwanted blob download requests).
            const { htmlBody, textBody } = await composerRef.current.exportContent();

            stopAutoSave();
            // Send (and "send and archive") moves the thread out of the drafts
            // filter — and possibly out of the inbox when archived. Drop the
            // pin upfront so the eventual refetch is authoritative.
            const draftThreadId = draft?.thread_id ?? selectedThread?.id;
            if (draftThreadId) unpinThreads([draftThreadId]);
            messageMutation.mutate({
                data: {
                    messageId,
                    senderId: data.from,
                    htmlBody: MailHelper.attachDriveAttachmentsToHtmlBody(htmlBody, data.driveAttachments),
                    textBody: MailHelper.attachDriveAttachmentsToTextBody(textBody, data.driveAttachments),
                    archive,
                }
            });
        } catch (error) {
            console.warn("Error in handleSubmit:", error);
            setIsSubmitting(false);
        }
    };

    /**
     * Prevent the Enter key press to trigger onClick on input children (like file input)
     */
    const handleKeyDown = (event: React.KeyboardEvent) => {
        if (event.key !== 'Enter') return;
        const target = event.target as HTMLElement;
        if (target.isContentEditable || target.tagName === 'TEXTAREA') return;
        event.preventDefault();
    }

    // The periodic auto-save only runs while a draft exists. Before that, the
    // draft is created on demand from real user content, not on a timer.
    useEffect(() => {
        if (draft) startAutoSave();
        else stopAutoSave();
        return () => stopAutoSave();
    }, [draft]);

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
                onSubmit={form.handleSubmit(() => handleSubmit({ archive: preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE }))}
                onBlur={form.handleSubmit(saveDraft)}
                onKeyDown={handleKeyDown}
            >
                <div className={clsx("form-field-row", { 'form-field-row--hidden': hideFromField })}>
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
                        autoFocus={mode === "forward"}
                        text={form.formState.errors.to && !Array.isArray(form.formState.errors.to) ? form.formState.errors.to.message : t("Enter the email addresses of the recipients separated by commas")}
                        textItems={Array.isArray(form.formState.errors.to) ? form.formState.errors.to?.map((error, index) => t(error!.message as string, { email: form.getValues('to')?.[index] })) : []}
                        disabled={!canWriteMessages}
                        rightText={
                            <div className="form-field-options">
                                <Button tabIndex={-1} type="button" size="nano" variant={showCCField ? "bordered" : "tertiary"} onClick={() => setShowCCField(!showCCField)} disabled={!canWriteMessages}>{t("cc")}</Button>
                                <Button tabIndex={-1} type="button" size="nano" variant={showBCCField ? "bordered" : "tertiary"} onClick={() => setShowBCCField(!showBCCField)} disabled={!canWriteMessages}>{t("bcc")}</Button>
                            </div> as unknown as string // TODO: Allow ReactNode as rightText in Cunningham
                        }
                        fullWidth
                        clearable
                    />
                </div>

                {showCCField && (
                    <div className="form-field-row">
                        <RhfContactComboBox
                            name="cc"
                            label={t("Copy: ")}
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
                            text={form.formState.errors.bcc && !Array.isArray(form.formState.errors.bcc) ? t(form.formState.errors.bcc.message as string) : t("Enter the email addresses of the recipients separated by commas")}
                            textItems={Array.isArray(form.formState.errors.bcc) ? form.formState.errors.bcc?.map((error, index) => t(error!.message as string, { email: form.getValues('bcc')?.[index] })) : []}
                            disabled={!canWriteMessages}
                            fullWidth
                            clearable
                        />
                    </div>
                )}

                <div className={clsx("form-field-row", { 'form-field-row--hidden': hideSubjectField })}>
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
                        ref={composerRef}
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
                        ensureDraft={ensureDraft}
                        blockNoteOptions={{ autofocus: canWriteMessages && mode !== "forward" ? "end" : undefined }}
                        uploadInlineImage={attachmentHook.uploadInlineImage}
                        uploadFiles={attachmentHook.uploadFiles}
                        removeInlineImage={attachmentHook.removeInlineImage}
                        attachments={attachmentHook.attachments}
                    />
                </div>

                {showAttachmentsForgetAlert &&
                    <Banner type="warning">
                        {t("Did you forget an attachment?")}
                    </Banner>
                }

                {showRecipientLimitWarning &&
                    <Banner type="warning">
                        {t("You have {{count}} recipients, which exceeds the maximum of {{max}} recipients per message. The message cannot be sent until you reduce the number of recipients.", { count: totalRecipients, max: config.MAX_RECIPIENTS_PER_MESSAGE })}
                    </Banner>
                }

                <AttachmentUploader
                    attachments={attachmentHook.attachments}
                    uploadingQueue={attachmentHook.uploadingQueue}
                    failedQueue={attachmentHook.failedQueue}
                    onUploadFiles={attachmentHook.uploadFiles}
                    onRemove={attachmentHook.removeAttachment}
                    onRemoveFailedUpload={attachmentHook.removeFailedUpload}
                    onRetry={attachmentHook.retryUpload}
                    onDriveAttachmentPick={attachmentHook.addDriveFiles}
                    disabled={!canWriteMessages}
                    maxAttachmentSize={attachmentHook.maxAttachmentSize}
                />


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
                        variant="primary"
                        disabled={!canSendMessages || isSubmittingMessage || showRecipientLimitWarning || isDeletingDraft}
                        icon={isSubmittingMessage ? <Spinner size="sm" /> : undefined}
                        type="submit"
                        dropdownOptions={[
                            ...(mode !== 'new' ? [{
                                label: preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE ? t("Send") : t("Send and archive"),
                                icon: <Icon name={preferredSendMode === PreferSendMode.SEND_AND_ARCHIVE ? "send" : "send_and_archive"} type={IconType.OUTLINED} />,
                                callback: form.handleSubmit(() => handleSubmit({ archive: preferredSendMode !== PreferSendMode.SEND_AND_ARCHIVE })),
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
                                variant="tertiary"
                                onClick={onClose}
                                aria-label={t("Delete")}
                                icon={<Icon name="delete" type={IconType.OUTLINED} />}
                            />
                        </Tooltip>
                    )}
                    {
                        canDeleteDraft && (
                            <Tooltip content={t("Delete draft")} placement="top">
                                <Button
                                    type="button"
                                    variant="tertiary"
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
});

MessageForm.displayName = "MessageForm";

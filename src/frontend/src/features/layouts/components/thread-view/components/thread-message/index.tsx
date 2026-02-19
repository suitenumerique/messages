import { useState, useCallback, forwardRef, useEffect, useRef, useMemo } from "react";
import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useConfig } from "@/features/providers/config";
import { Banner } from "@/features/ui/components/banner";
import { MessageDeliveryStatusChoices } from "@/features/api/gen/models";
import { useMessagesDeliveryStatusesPartialUpdate } from "@/features/api/gen/messages/messages";
import { MessageFormMode } from "@/features/forms/components/message-form";
import MailHelper from "@/features/utils/mail-helper";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useThreadViewContext } from "../../provider";
import usePrevious from "@/hooks/use-previous";
import ThreadMessageBody from "./thread-message-body";
import MessageReplyForm from "../message-reply-form";
import ThreadMessageHeader from "./thread-message-header";
import ThreadMessageFooter from "./thread-message-footer";
import { ThreadMessageProps } from "./types";
import { BodyPart } from "./renderers";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";

export const ThreadMessage = forwardRef<HTMLSpanElement, ThreadMessageProps>(
    ({ message, isLatest, draftMessage, ...props }, ref) => {
        const { t } = useTranslation();
        const replyFormRef = useRef<HTMLDivElement>(null);
        const threadViewContext = useThreadViewContext();
        const { selectedMailbox, selectedThread, queryStates, invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
        const config = useConfig();
        const shouldSkipDelivery = !message.is_sender || message.is_draft || message.is_trashed;

        // Refresh dateNow periodically to keep canRetry accurate over time
        // Only active when the message has a problematic delivery status
        const [dateNow, setDateNow] = useState(Date.now);
        const hasDeliveryIssue = useMemo(() => {
            if (shouldSkipDelivery) return false;
            const allRecipients = [...message.to, ...message.cc, ...message.bcc];
            return allRecipients.some(r =>
                r.delivery_status === MessageDeliveryStatusChoices.failed ||
                r.delivery_status === MessageDeliveryStatusChoices.retry ||
                r.delivery_status === null
            );
        }, [message.is_sender, message.to, message.cc, message.bcc]);

        const canSendMessages = useAbility(Abilities.CAN_SEND_MESSAGES, selectedMailbox);
        const canUpdateDeliveryStatus = useAbility(Abilities.CAN_MANAGE_THREAD_DELIVERY_STATUSES, [selectedMailbox!, selectedThread!]);

        // Derived state
        const isMessageReady = threadViewContext.isMessageReady(message.id);
        const hasSeveralRecipients = useMemo(() => {
            return message.to.length + message.cc.length > 1;
        }, [message.to.length, message.cc.length]);

        // Compute if manual retry is allowed based on message age
        const canRetry = useMemo(() => {
            if (shouldSkipDelivery || !message.sent_at) return false;
            const maxAgeMs = config.MESSAGES_MANUAL_RETRY_MAX_AGE * 1000;
            const messageAgeMs = dateNow - new Date(message.sent_at).getTime();
            return messageAgeMs <= maxAgeMs;
        }, [message.is_sender, message.sent_at, config.MESSAGES_MANUAL_RETRY_MAX_AGE, dateNow]);

        const deliveryStatus = useMemo(() => {
            if (shouldSkipDelivery) return null;
            const allRecipients = [...message.to, ...message.cc, ...message.bcc];
            const hasFailed = allRecipients.some(r => r.delivery_status === MessageDeliveryStatusChoices.failed);
            const hasRetry = allRecipients.some(r => r.delivery_status === MessageDeliveryStatusChoices.retry || r.delivery_status === null);
            if (hasFailed) return 'failed';
            if (hasRetry) return 'retry';
            return null;
        }, [message.is_sender, message.to, message.cc, message.bcc]);

        // Extract drive attachments from HTML body parts
        const [processedHtmlBody, driveAttachments] = useMemo((): [BodyPart[], DriveFile[]] => {
            if (message.htmlBody.length === 0) {
                return [[], []] as const;
            }
            // Process each HTML body part for drive attachments
            const allDriveAttachments: ReturnType<typeof MailHelper.extractDriveAttachmentsFromHtmlBody>[1] = [];
            const processedParts = message.htmlBody.map(part => {
                const partContent = part?.content || "";
                const partType = part?.type || "text/html";
                const partId = part?.partId || "";
                const [content, attachments] = MailHelper.extractDriveAttachmentsFromHtmlBody(partContent);
                allDriveAttachments.push(...attachments);
                return { partId, type: partType, content };
            });
            return [processedParts, allDriveAttachments] as const;
        }, [message.htmlBody]);

        // Process text body parts
        const processedTextBody = useMemo(() => {
            if (message.textBody.length === 0) {
                return [];
            }
            return message.textBody.map(part => {
                const partContent = part?.content || "";
                const partType = part?.type || "text/plain";
                const partId = part?.partId || "";
                // Extract and process drive attachment URLs from text content
                const [content] = MailHelper.extractDriveAttachmentsFromTextBody(partContent);
                return { partId, type: partType, content };
            });
        }, [message.textBody]);

        // Determine which body parts to render (prefer HTML if available)
        const bodyPartsToRender = processedHtmlBody.length > 0 ? processedHtmlBody : processedTextBody;

        // Component state
        const [isThreadMessageBodyLoaded, setIsThreadMessageBodyLoaded] = useState(isMessageReady);
        const [isFolded, setIsFolded] = useState(!isLatest && !message.is_unread && !draftMessage?.is_draft);
        const [replyFormMode, setReplyFormMode] = useState<MessageFormMode | null>(() => {
            if (draftMessage?.is_draft) return 'reply';
            if (!message.is_draft || message.is_trashed) return null;
            return 'new';
        });
        const previousReplyFormMode = usePrevious<MessageFormMode | null>(replyFormMode);

        // Computed flags
        const showReplyForm = replyFormMode !== null;
        const showReplyButton = canSendMessages && isLatest && !showReplyForm && !message.is_draft && !message.is_trashed && !draftMessage;

        // Handlers
        const toggleFold = useCallback(() => {
            setIsFolded(prev => !prev);
        }, []);

        const handleCloseReplyForm = useCallback(() => {
            setReplyFormMode(null);
        }, []);

        // Mutation to update delivery statuses
        const { mutate: updateDeliveryStatus } = useMessagesDeliveryStatusesPartialUpdate();

        // Get recipients with failed or retry status
        const failedRecipients = useMemo(() => {
            const allRecipients = [...message.to, ...message.cc, ...message.bcc];
            return allRecipients.filter(r => r.delivery_status === MessageDeliveryStatusChoices.failed);
        }, [message.to, message.cc, message.bcc]);

        const retryRecipients = useMemo(() => {
            const allRecipients = [...message.to, ...message.cc, ...message.bcc];
            return allRecipients.filter(r => r.delivery_status === MessageDeliveryStatusChoices.retry);
        }, [message.to, message.cc, message.bcc]);

        const handleDismissFailures = useCallback(() => {
            // Build the payload with recipient IDs mapped to 'cancelled' status
            const data: Record<string, string> = {};
            failedRecipients.forEach(r => {
                data[r.id] = 'cancelled';
            });

            updateDeliveryStatus({ id: message.id, data }, {
                onSuccess: () => {
                    invalidateThreadMessages();
                    invalidateThreadsStats();
                }
            });
        }, [message.id, failedRecipients, updateDeliveryStatus, invalidateThreadMessages, invalidateThreadsStats]);

        const handleRetryFailures = useCallback(() => {
            // Build the payload with recipient IDs mapped to 'retry' status
            const data: Record<string, MessageDeliveryStatusChoices> = {};
            failedRecipients.forEach(r => {
                data[r.id] = MessageDeliveryStatusChoices.retry;
            });

            updateDeliveryStatus({ id: message.id, data }, {
                onSuccess: () => {
                    invalidateThreadMessages();
                    invalidateThreadsStats();
                }
            });
        }, [message.id, failedRecipients, updateDeliveryStatus, invalidateThreadMessages, invalidateThreadsStats]);

        const handleCancelRetries = useCallback(() => {
            // Build the payload with recipient IDs mapped to 'cancelled' status
            const data: Record<string, MessageDeliveryStatusChoices> = {};
            retryRecipients.forEach(r => {
                data[r.id] = MessageDeliveryStatusChoices.cancelled;
            });

            updateDeliveryStatus({ id: message.id, data }, {
                onSuccess: () => {
                    invalidateThreadMessages();
                    invalidateThreadsStats();
                }
            });
        }, [message.id, retryRecipients, updateDeliveryStatus, invalidateThreadMessages, invalidateThreadsStats]);

        // Handler for individual recipient status updates
        const handleUpdateRecipientStatus = useCallback((recipientId: string, status: 'cancelled' | 'retry') => {
            const data: Record<string, MessageDeliveryStatusChoices> = { [recipientId]: status };

            updateDeliveryStatus({ id: message.id, data }, {
                onSuccess: () => {
                    invalidateThreadMessages();
                    invalidateThreadsStats();
                }
            });
        }, [message.id, updateDeliveryStatus, invalidateThreadMessages, invalidateThreadsStats]);

        // Effects
        useEffect(() => {
            const getReplyFormMode = (): MessageFormMode | null => {
                if (draftMessage?.is_draft) return 'reply';
                if (!message.is_draft || message.is_trashed) return null;
                return 'new';
            };
            setReplyFormMode(getReplyFormMode());
        }, [message, draftMessage]);

        // Smooth scroll to the reply form when it is opened by the user
        useEffect(() => {
            if (!threadViewContext.isReady) return;
            if (previousReplyFormMode === null && showReplyForm !== null) {
                if (replyFormRef.current) {
                    const container = document.querySelector<HTMLElement>('.thread-view')!;
                    container.scrollTo({ behavior: 'smooth', top: replyFormRef.current.offsetTop - 225 });
                }
            }
        }, [showReplyForm, threadViewContext.isReady, previousReplyFormMode]);

        useEffect(() => {
            if (isThreadMessageBodyLoaded && !queryStates.messages.isFetching) {
                threadViewContext.setMessageReadiness(message.id, true);
            }
        }, [isThreadMessageBodyLoaded, queryStates.messages.isFetching, message.id]);

        useEffect(() => {
            if (!hasDeliveryIssue) return;
            const intervalId = setInterval(() => setDateNow(Date.now()), 60_000);
            return () => clearInterval(intervalId);
        }, [hasDeliveryIssue]);

        return (
            <section
                id={`thread-message-${message.id}`}
                className={clsx("thread-message", {
                    "thread-message--folded": isFolded || !isMessageReady,
                    "thread-message--sender": message.is_sender,
                    "thread-message--delivery-failed": deliveryStatus === 'failed',
                    "thread-message--delivery-retry": deliveryStatus === 'retry',
                })}
                data-unread={message.is_unread}
                data-trashed={message.is_trashed}
                {...props}
            >
                {deliveryStatus === 'failed' && (
                    <Banner
                        icon={<Icon name="error" type={IconType.OUTLINED} />}
                        type="error"
                        fullWidth
                        actions={canUpdateDeliveryStatus ? [
                            ...(canRetry ? [{
                                label: t('Retry'),
                                onClick: handleRetryFailures,
                                color: "error" as const,
                                variant: "secondary" as const,
                            }] : []),
                            {
                                label: t('Cancel those sendings'),
                                onClick: handleDismissFailures,
                                color: "error",
                                variant: "secondary",
                            }
                        ] : undefined}
                    >
                        <p>{t('Some recipients have not received this message!')}</p>
                    </Banner>
                )}
                {deliveryStatus === 'retry' && (
                    <Banner
                        icon={<Icon name="update" type={IconType.OUTLINED} />}
                        type="warning"
                        fullWidth
                        actions={canUpdateDeliveryStatus ? [
                            {
                                label: t('Cancel those sendings'),
                                onClick: handleCancelRetries,
                                variant: "secondary",
                            }
                        ] : undefined}
                    >
                        <p>{t('This message has not yet been delivered to all recipients.')}</p>
                    </Banner>
                )}

                <ThreadMessageHeader
                    message={message}
                    draftMessage={draftMessage}
                    isLatest={isLatest}
                    isFolded={isFolded}
                    canSendMessages={canSendMessages}
                    canRetry={canRetry}
                    hasSeveralRecipients={hasSeveralRecipients}
                    onToggleFold={toggleFold}
                    onSetReplyFormMode={setReplyFormMode}
                    onUpdateRecipientStatus={canUpdateDeliveryStatus ? handleUpdateRecipientStatus : undefined}
                />

                <ThreadMessageBody
                    bodyParts={bodyPartsToRender}
                    attachments={message.attachments}
                    messageId={message.id}
                    isHidden={isFolded || !isMessageReady}
                    onLoad={() => setIsThreadMessageBodyLoaded(true)}
                />

                <ThreadMessageFooter
                    message={message}
                    driveAttachments={driveAttachments}
                    showReplyButton={showReplyButton}
                    hasSeveralRecipients={hasSeveralRecipients}
                    onSetReplyFormMode={setReplyFormMode}
                    intersectionRef={ref}
                />

                {isMessageReady && showReplyForm && (
                    <section className="thread-message__reply-form" ref={replyFormRef}>
                        <MessageReplyForm
                            mode={replyFormMode}
                            handleClose={handleCloseReplyForm}
                            message={draftMessage || message}
                        />
                    </section>
                )}

                {!isFolded && !isMessageReady && (
                    <div className="thread-message__loading">
                        <Spinner />
                    </div>
                )}
            </section>
        );
    }
);

ThreadMessage.displayName = "ThreadMessage";

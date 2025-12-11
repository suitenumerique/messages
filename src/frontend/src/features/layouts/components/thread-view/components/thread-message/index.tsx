import { useMemo, useState, useCallback, forwardRef, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { DropdownMenu, Icon, IconSize, IconType, Spinner, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { Message, MessageDeliveryStatusChoices, MessageRecipient } from "@/features/api/gen/models";
import useRead from "@/features/message/use-read";
import { useMailboxContext } from "@/features/providers/mailbox";
import { Badge } from "@/features/ui/components/badge";
import useTrash from "@/features/message/use-trash";
import MessageBody from "./message-body"
import MessageReplyForm from "../message-reply-form";
import { AttachmentList } from "../thread-attachment-list";
import { Banner } from "@/features/ui/components/banner";
import { MessageFormMode } from "@/features/forms/components/message-form";
import MailHelper from "@/features/utils/mail-helper";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { getMessagesEmlRetrieveUrl } from "@/features/api/gen/messages/messages";
import { getRequestUrl } from "@/features/api/utils";
import { ContactChip, ContactChipDeliveryStatus } from "@/features/ui/components/contact-chip";
import clsx from "clsx";
import { useThreadViewContext } from "../../provider";
import usePrevious from "@/hooks/use-previous";

type ThreadMessageProps = {
    message: Message,
    isLatest: boolean,
    draftMessage?: Message
} & React.HTMLAttributes<HTMLElement>

export const ThreadMessage = forwardRef<HTMLElement, ThreadMessageProps>(
    ({ message, isLatest, draftMessage, ...props }, ref) => {
        const { t, i18n } = useTranslation()
        const getReplyFormMode = () => {
            if (draftMessage?.is_draft) return 'reply';
            if (!message.is_draft || message.is_trashed) return null;
            return 'new';
        }
        const replyFormRef = useRef<HTMLDivElement>(null);
        const threadViewContext = useThreadViewContext()
        const isMessageReady = threadViewContext.isMessageReady(message.id);
        const [isMessageBodyLoaded, setIsMessageBodyLoaded] = useState(isMessageReady);
        const [isFolded, setIsFolded] = useState(!isLatest && !message.is_unread && !draftMessage?.is_draft);
        const [replyFormMode, setReplyFormMode] = useState<MessageFormMode | null>(getReplyFormMode)
        const previousReplyFormMode = usePrevious<MessageFormMode | null>(replyFormMode);
        const { unselectThread, selectedThread, messages, selectedMailbox, queryStates } = useMailboxContext()
        const showReplyForm = replyFormMode !== null;
        const isSuspiciousSender = Boolean(message.stmsg_headers?.['sender-auth'] === 'none');
        const { markAsUnread } = useRead()
        const { markAsTrashed, markAsUntrashed } = useTrash()
        const [isDropdownOpen, setIsDropdownOpen] = useState(false)
        const canSendMessages = useAbility(Abilities.CAN_SEND_MESSAGES, selectedMailbox);
        const hasSiblingMessages = useMemo(() => {
            if (!selectedThread) return false;
            return selectedThread?.messages?.length > 1;
        }, [selectedThread])
        const hasSeveralRecipients = useMemo(() => {
            return message.to.length + message.cc.length > 1;
        }, [message])
        const showReplyButton = canSendMessages && isLatest && !showReplyForm && !message.is_draft && !message.is_trashed && !draftMessage
        const toggleFold = () => {
            setIsFolded(!isFolded);
        }

        const [htmlBody, driveAttachments] = MailHelper.extractDriveAttachmentsFromHtmlBody(message.htmlBody[0]?.content as string);
        const [textBody,] = MailHelper.extractDriveAttachmentsFromTextBody(message.textBody[0]?.content as string);

        const getRecipientDeliveryStatus = (recipient: MessageRecipient): ContactChipDeliveryStatus | undefined => {
            // If the message has just been sent, it has not delivery status but for the sender it is useful to show that the message is being delivered
            if (message.is_sender && recipient.delivery_status === null && !message.is_draft) {
                return { 'status': 'delivering', 'timestamp': null, 'message': null };
            }
            switch (recipient.delivery_status) {
                case MessageDeliveryStatusChoices.failed:
                    return { 'status': 'undelivered', 'timestamp': recipient.retry_at, 'message': recipient.delivery_message };
                case MessageDeliveryStatusChoices.retry:
                    return { 'status': 'delivering', 'timestamp': recipient.retry_at, 'message': recipient.delivery_message };
                case MessageDeliveryStatusChoices.sent:
                case MessageDeliveryStatusChoices.internal:
                    return { 'status': 'delivered', 'timestamp': recipient.delivered_at, 'message': recipient.delivery_message };
                default:
                    return undefined;
            }
        }

        const handleCloseReplyForm = () => {
            setReplyFormMode(null);
        }

        const markAsUnreadFrom = useCallback((messageId: Message['id']) => {
            const offsetIndex = messages?.findIndex((m) => m.id === messageId) ?? -1;
            if (offsetIndex < 0) return;
            const messageIds = messages?.slice(offsetIndex).map((m) => m.id);
            markAsUnread({ messageIds, onSuccess: unselectThread });
        }, [messages, unselectThread, markAsUnread])

        useEffect(() => {
            setReplyFormMode(getReplyFormMode())
        }, [message, draftMessage])

        // Smooth scroll to the reply form when it is opened by the user
        useEffect(() => {
            if (!threadViewContext.isReady) return;
            if (previousReplyFormMode === null && showReplyForm !== null) {
                if (replyFormRef.current) {
                    const container = document.querySelector<HTMLElement>('.thread-view')!;
                    container.scrollTo({ behavior: 'smooth', top: replyFormRef.current.offsetTop - 225 });
                }
            }
        }, [showReplyForm, threadViewContext.isReady]);

        useEffect(() => {
            if (isMessageBodyLoaded && !queryStates.messages.isFetching) {
                threadViewContext.setMessageReadiness(message.id, true);
            }
        }, [isMessageBodyLoaded, queryStates.messages.isFetching, message.id]);

        return (
            <section id={`thread-message-${message.id}`} className={clsx("thread-message", {
                "thread-message--folded": isFolded || !isMessageReady,
                "thread-message--sender": message.is_sender,
            })} data-unread={message.is_unread} data-trashed={message.is_trashed} {...props}>
                <header className="thread-message__header">
                    <button
                        className="thread-message__header-toggle"
                        onClick={toggleFold}
                        disabled={isLatest}
                        aria-hidden={isLatest}
                        aria-label={isFolded ? t('Unfold message') : t('Fold message')}
                        title={isFolded ? t('Unfold message') : t('Fold message')}
                    />
                    <div>
                        {
                            message.is_trashed && (
                                <Banner type="info" icon={<Icon name="info" type={IconType.OUTLINED} />} fullWidth>
                                    <div className="thread-view__trashed-banner__content">
                                        <p>{t('This message has been deleted.')}</p>
                                        <div className="thread-view__trashed-banner__actions">
                                            <Button
                                                onClick={() => markAsUntrashed({ messageIds: [message.id] })}
                                                color="primary-text"
                                                size="small"
                                                icon={<span className="material-icons">restore_from_trash</span>}
                                            >
                                                {t('Undelete')}
                                            </Button>
                                        </div>
                                    </div>
                                </Banner>
                            )}
                        <div className="thread-message__header-rows" style={{ marginBottom: 'var(--c--theme--spacings--sm)' }}>
                            {isSuspiciousSender && (
                                <Banner type="warning" compact fullWidth>
                                    <div className="thread-message__header-banner__content">
                                        <p>{t("This contact's identity could not be verified. Proceed with caution.")}</p>
                                    </div>
                                </Banner>
                            )}
                        </div>
                        <div className="thread-message__header-content">
                            <div className="thread-message__header-content-avatar">
                                <UserAvatar fullName={message.sender.name || message.sender.email} />
                            </div>
                            <div className="thread-message__header-content-info">
                                <div className="thread-message__header-rows">
                                    <div className="thread-message__header-column thread-message__header-column--left flex-row flex-align-center">
                                        <ContactChip
                                            className="thread-message__sender-chip"
                                            contact={message.sender}
                                            isUser={message.is_sender}
                                            status={isSuspiciousSender ? 'unverified' : undefined}
                                            displayEmail
                                        />
                                    </div>
                                    <div className="thread-message__header-column thread-message__header-column--right flex-row flex-align-center">
                                        <div className="thread-message__metadata">
                                            {message.created_at && (
                                                <p className="thread-message__date">{
                                                    new Date(message.created_at).toLocaleString(i18n.resolvedLanguage, {
                                                        minute: '2-digit',
                                                        hour: '2-digit',
                                                        day: 'numeric',
                                                        month: 'short',
                                                        year: 'numeric',
                                                    })
                                                }</p>
                                            )}
                                            {message.is_draft && (
                                                <Badge>
                                                    {t('Draft')}
                                                </Badge>
                                            )}
                                            {
                                                message.attachments.length > 0 && (
                                                    <span className="material-icons">attachment</span>
                                                )
                                            }
                                        </div>
                                        <div className="thread-message__header-actions">
                                            {!isFolded && (
                                                <>
                                                    {canSendMessages && (
                                                        <Tooltip content={t('Reply')}>
                                                            <Button
                                                                color="tertiary-text"
                                                                size="small"
                                                                icon={<span className="material-icons">reply</span>}
                                                                aria-label={t('Reply')}
                                                                onClick={() => setReplyFormMode('reply')}
                                                            />
                                                        </Tooltip>
                                                    )}
                                                    <DropdownMenu
                                                        isOpen={isDropdownOpen}
                                                        onOpenChange={setIsDropdownOpen}
                                                        options={[
                                                            ...(canSendMessages && hasSeveralRecipients ? [{
                                                                label: t('Reply all'),
                                                                icon: <span className="material-icons">reply_all</span>,
                                                                callback: () => setReplyFormMode('reply_all')
                                                            }] : []),
                                                            ...(canSendMessages ? [{
                                                                label: t('Forward'),
                                                                icon: <span className="material-icons">forward</span>,
                                                                callback: () => setReplyFormMode('forward'),
                                                                showSeparator: true
                                                            }] : []),
                                                            {
                                                                label: hasSiblingMessages ? t('Mark as unread from here') : t('Mark as unread'),
                                                                icon: <span className="material-icons">mark_email_unread</span>,
                                                                callback: () => markAsUnreadFrom(message.id)
                                                            },
                                                            {
                                                                label: t('Download raw email'),
                                                                icon: <span className="material-icons">download</span>,
                                                                callback: () => {
                                                                    const downloadUrl = getRequestUrl(getMessagesEmlRetrieveUrl(message.id));
                                                                    const link = document.createElement('a');
                                                                    link.href = downloadUrl;
                                                                    link.download = `message-${message.id}.eml`;
                                                                    document.body.appendChild(link);
                                                                    link.click();
                                                                    document.body.removeChild(link);
                                                                }
                                                            },
                                                            ...(message.is_trashed ? [] : [{
                                                                label: t('Delete'),
                                                                icon: <span className="material-icons">delete</span>,
                                                                callback: () => markAsTrashed({ messageIds: [message.id] })
                                                            }]),
                                                        ]}
                                                    >
                                                        <Tooltip content={t('More options')}>
                                                            <Button
                                                                onClick={() => setIsDropdownOpen(true)}
                                                                icon={<span className="material-icons">more_vert</span>}
                                                                color="primary-text"
                                                                aria-label={t('More options')}
                                                                size="small"
                                                            />
                                                        </Tooltip>
                                                    </DropdownMenu>
                                                </>
                                            )}
                                            {
                                                !isLatest && (
                                                    <Tooltip content={isFolded ? t('Unfold message') : t('Fold message')}>
                                                        <Button
                                                            color="tertiary-text"
                                                            size="small"
                                                            icon={<Icon type={IconType.FILLED} name={isFolded ? "unfold_more" : "unfold_less"} size={IconSize.LARGE} />}
                                                            aria-label={isFolded ? t('Unfold message') : t('Fold message')}
                                                            onClick={toggleFold}
                                                        />
                                                    </Tooltip>
                                                )
                                            }
                                        </div>
                                    </div>
                                </div>
                                <div className="thread-message__header-rows">
                                    <div className="thread-message__header-column thread-message__header-column--left">
                                        <dl className="thread-message__correspondents">
                                            {message.to.length > 0 && (
                                                <>
                                                    <dt>{t('To: ')}</dt>
                                                    <dd className="recipient-chip-list">
                                                        {message.to.map((recipient) => (
                                                            <ContactChip
                                                                key={`to-${recipient.contact.id}`}
                                                                contact={recipient.contact}
                                                                status={getRecipientDeliveryStatus(recipient)}
                                                            />
                                                        ))}
                                                    </dd>
                                                </>
                                            )}
                                            {message.cc.length > 0 && (
                                                <>
                                                    <dt>{t('Copy: ')}</dt>
                                                    <dd className="recipient-chip-list">
                                                        {message.cc.map((recipient) => (
                                                            <ContactChip
                                                                key={`cc-${recipient.contact.id}`}
                                                                contact={recipient.contact}
                                                                status={getRecipientDeliveryStatus(recipient)}
                                                            />
                                                        ))}
                                                    </dd>
                                                </>
                                            )}
                                            {message.bcc.length > 0 && (
                                                <>
                                                    <dt>{t('BCC: ')}</dt>
                                                    <dd className="recipient-chip-list">
                                                        {message.bcc.map((recipient) => (
                                                            <ContactChip
                                                                key={`bcc-${recipient.contact.id}`}
                                                                contact={recipient.contact}
                                                                status={getRecipientDeliveryStatus(recipient)}
                                                            />
                                                        ))}
                                                    </dd>
                                                </>
                                            )}
                                        </dl>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </header>
                <MessageBody
                    rawTextBody={textBody}
                    rawHtmlBody={htmlBody}
                    attachments={message.attachments}
                    isHidden={isFolded || !isMessageReady}
                    onLoad={() => {
                        setIsMessageBodyLoaded(true);
                    }}
                />
                <footer className="thread-message__footer">
                    <span className="thread-message__intersection-trigger" ref={ref} data-message-id={message.id} />
                    {!message.is_draft && (message.attachments.length > 0 || driveAttachments.length > 0) && (
                        <AttachmentList attachments={[...message.attachments, ...driveAttachments]} />
                    )}
                    {
                        showReplyButton && (
                            <div className="thread-message__footer-actions">
                                {hasSeveralRecipients && (
                                    <Button
                                        color="primary"
                                        icon={<span className="material-icons">reply_all</span>}
                                        aria-label={t('Reply all')}
                                        onClick={() => setReplyFormMode('reply_all')}
                                    >
                                        {t('Reply all')}
                                    </Button>
                                )}
                                <Button
                                    color={hasSeveralRecipients ? 'secondary' : 'primary'}
                                    icon={<span className="material-icons">reply</span>}
                                    aria-label={t('Reply')}
                                    onClick={() => setReplyFormMode('reply')}
                                >
                                    {t('Reply')}
                                </Button>
                                <Button
                                    color='secondary'
                                    icon={<span className="material-icons">forward</span>}
                                    onClick={() => setReplyFormMode('forward')}
                                >
                                    {t('Forward')}
                                </Button>
                            </div>
                        )
                    }
                </footer>
                {isMessageReady && showReplyForm &&
                    <section className="thread-message__reply-form" ref={replyFormRef}>
                        <MessageReplyForm
                            mode={replyFormMode}
                            handleClose={handleCloseReplyForm}
                            message={draftMessage || message}
                        />
                    </section>
                }
                {!isFolded && !isMessageReady && (
                    <div className="thread-message__loading">
                        <Spinner />
                    </div>
                )}
            </section>
        )
    }
);

ThreadMessage.displayName = "ThreadMessage";

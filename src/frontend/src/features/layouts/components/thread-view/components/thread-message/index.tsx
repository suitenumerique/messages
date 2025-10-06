import { useMemo, useState, useCallback, forwardRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit";
import { Message } from "@/features/api/gen/models";
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
import { ContactChip } from "@/features/ui/components/contact-chip";

type ThreadMessageProps = {
    message: Message,
    isLatest: boolean,
    draftMessage?: Message
} & React.HTMLAttributes<HTMLElement>

export const ThreadMessage = forwardRef<HTMLElement, ThreadMessageProps>(
    ({ message, isLatest, draftMessage, ...props }, ref) => {
        const { t, i18n } = useTranslation()
        const [replyFormMode, setReplyFormMode] = useState<MessageFormMode | null>(() => {
            if (!message.is_trashed && (message.is_draft || draftMessage?.is_draft)) return 'reply';
            return null;
        })
        const showReplyForm = replyFormMode !== null;
        const isSuspiciousSender = Boolean(message.stmsg_headers?.['sender-auth'] === 'none');
        const { markAsUnread } = useRead()
        const { markAsTrashed, markAsUntrashed } = useTrash()
        const { unselectThread, selectedThread, messages, queryStates, selectedMailbox } = useMailboxContext()
        const isFetchingMessages = queryStates.messages.isFetching;
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

        const [htmlBody, driveAttachments] = MailHelper.extractDriveAttachmentsFromHtmlBody(message.htmlBody[0]?.content as string);
        const [textBody,] = MailHelper.extractDriveAttachmentsFromTextBody(message.textBody[0]?.content as string);

        const handleCloseReplyForm = () => {
            setReplyFormMode(null);
        }

        const markAsUnreadFrom = useCallback((messageId: Message['id']) => {
            const offestIndex = messages?.results.findIndex((m) => m.id === messageId);
            const messageIds = messages?.results.slice(offestIndex).map((m) => m.id);
            return markAsUnread({ messageIds, onSuccess: unselectThread });
        }, [messages, unselectThread, markAsUnread])

        useEffect(() => {
            setReplyFormMode(!message.is_trashed && (message.is_draft || draftMessage?.is_draft) ? 'reply' : null);
        }, [message, draftMessage])

        return (
            <section ref={ref} className="thread-message" data-unread={message.is_unread} data-trashed={message.is_trashed} {...props}>
                <header className="thread-message__header">
                    {
                        message.is_trashed && (
                            <Banner type="info" icon={<span className="material-icons">info</span>}>
                                <div className="thread-view__trashed-banner__content">
                                    <p>{t('This message has been deleted.')}</p>
                                    <div className="thread-view__trashed-banner__actions">
                                        <Button
                                            onClick={() => markAsUntrashed({messageIds: [message.id]})}
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
                    <div className="thread-message__header-rows">
                        <div className="thread-message__header-column thread-message__header-column--left">
                            <h2 className="thread-message__subject">{message.subject || selectedThread?.snippet || t('No subject')}</h2>
                        </div>
                        <div className="thread-message__header-column thread-message__header-column--right flex-row flex-align-center">
                            <div className="thread-message__metadata">
                                {message.sent_at && (
                                    <p className="thread-message__date">{
                                        new Date(message.sent_at).toLocaleString(i18n.resolvedLanguage, {
                                            minute: '2-digit',
                                            hour: '2-digit',
                                            day: '2-digit',
                                            month: '2-digit',
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
                                {canSendMessages && hasSeveralRecipients && (
                                    <Tooltip content={t('Reply all')}>
                                        <Button
                                            color="tertiary-text"
                                            size="small"
                                            icon={<span className="material-icons">reply_all</span>}
                                            aria-label={t('Reply all')}
                                            onClick={() => setReplyFormMode('reply_all')}
                                        />
                                    </Tooltip>
                                )}
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
                                {canSendMessages && (
                                    <Tooltip content={t('Forward')}>
                                        <Button
                                            color="tertiary-text"
                                        size="small"
                                        icon={<span className="material-icons">forward</span>}
                                        aria-label={t('Forward')}
                                        onClick={() => setReplyFormMode('forward')}
                                        />
                                    </Tooltip>
                                )}
                                <DropdownMenu
                                    isOpen={isDropdownOpen}
                                    onOpenChange={setIsDropdownOpen}
                                    options={[
                                        {
                                            label: hasSiblingMessages ? t('Mark as unread from here') : t('Mark as unread'),
                                            icon: <span className="material-icons">mark_email_unread</span>,
                                            callback: () => markAsUnreadFrom(message.id)
                                        },
                                        ...(message.is_trashed ? [] : [{
                                            label: t('Delete'),
                                            icon: <span className="material-icons">delete</span>,
                                            callback: () => markAsTrashed({messageIds: [message.id]})
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
                            </div>
                        </div>
                    </div>
                    { isSuspiciousSender && (
                    <div className="thread-message__header-rows" style={{ marginBlock: 'var(--c--theme--spacings--xs)' }}>
                        <Banner type="warning" compact fullWidth>
                            <div className="thread-message__header-banner__content">
                                <p>{t(t('The sender of this message cannot be trusted. Be careful when interacting with it.'))}</p>
                                </div>
                            </Banner>
                        </div>
                    )}
                    <div className="thread-message__header-rows">
                        <div className="thread-message__header-column thread-message__header-column--left">
                            <dl className="thread-message__correspondents">
                                <dt>{t('From: ')}</dt>
                                <dd className="recipient-chip-list">
                                    <ContactChip
                                        contact={message.sender}
                                        showWarning={isSuspiciousSender}
                                    />
                                </dd>
                                <dt>{t('To: ')}</dt>
                                <dd className="recipient-chip-list">
                                    {message.to.map((recipient) => (
                                        <ContactChip
                                            key={recipient.id}
                                            contact={recipient}
                                        />
                                    ))}
                                </dd>
                                {message.cc.length > 0 && (
                                    <>
                                        <dt>{t('Copy: ')}</dt>
                                        <dd className="recipient-chip-list">
                                            {message.cc.map((recipient) => (
                                                <ContactChip
                                                    key={recipient.id}
                                                    contact={recipient}
                                                />
                                            ))}
                                        </dd>
                                    </>
                                )}
                            </dl>
                        </div>
                    </div>
                </header>
                <MessageBody
                    rawTextBody={textBody}
                    rawHtmlBody={htmlBody}
                />
                <footer className="thread-message__footer">
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
                    { !isFetchingMessages && showReplyForm && <MessageReplyForm
                        mode={replyFormMode}
                        handleClose={handleCloseReplyForm}
                        message={draftMessage || message}
                    />}
                </footer>
            </section>
        )
    }
);

ThreadMessage.displayName = "ThreadMessage";

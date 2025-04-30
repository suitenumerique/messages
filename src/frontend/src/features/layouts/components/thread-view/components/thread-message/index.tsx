import { useMemo, useState, useCallback, forwardRef } from "react";
import { useTranslation } from "react-i18next";
import { Message } from "@/features/api/gen/models";
import MessageBody from "./message-body"
import MessageReplyForm from "../message-reply-form";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit";
import useRead from "@/features/message/useRead";
import { useMailboxContext } from "@/features/mailbox/provider";
type ThreadMessageProps = {
    message: Message,
    isLatest: boolean
} & React.HTMLAttributes<HTMLElement>

export const ThreadMessage = forwardRef<HTMLElement, ThreadMessageProps>(
    ({ message, isLatest, ...props }, ref) => {
        const { t, i18n } = useTranslation()
        const [showReplyForm, setShowReplyForm] = useState<'all' | 'to' | null>(null)
        const { markAsUnread } = useRead()
        const { unselectThread, selectedThread, messages } = useMailboxContext()
        const [isDropdownOpen, setIsDropdownOpen] = useState(false)
        const hasSiblingMessages = useMemo(() => {
            if (!selectedThread) return false;
            return selectedThread?.messages?.length > 1;
        }, [selectedThread])
        const hasSeveralRecipients = useMemo(() => {
            return message.to.length + message.cc.length > 1;
        }, [message])

        const handleCloseReplyForm = () => {
            setShowReplyForm(null);
        }

        const markAsUnreadFrom = useCallback((messageId: Message['id']) => {
            const offestIndex = messages?.results.findIndex((m) => m.id === messageId);
            const messageIds = messages?.results.slice(offestIndex).map((m) => m.id);
            return markAsUnread({ messageIds, onSuccess: unselectThread });
        }, [messages, unselectThread, markAsUnread])

        return (
            <section ref={ref} className="thread-message" data-unread={message.is_unread} {...props}>
                <header className="thread-message__header">
                    <div className="thread-message__header-rows">
                        <div className="thread-message__header-column thread-message__header-column--left">
                            <h2 className="thread-message__subject">{message.subject}</h2>
                        </div>
                        <div className=" thread-message__header-column thread-message__header-column--right flex-row flex-align-center">
                            <p className="thread-message__date m-0">{new Date(message.sent_at!).toLocaleString(i18n.language, {
                                minute: '2-digit',
                                hour: '2-digit',
                                day: '2-digit',
                                month: '2-digit',
                                year: 'numeric',
                            })}</p>
                            <div className="thread-message__header-actions">
                                {hasSeveralRecipients && (
                                    <Tooltip content={t('actions.reply_all')}>
                                        <Button
                                            color="tertiary-text"
                                            size="small"
                                            icon={<span className="material-icons">reply_all</span>}
                                            aria-label={t('actions.reply_all')}
                                            onClick={() => setShowReplyForm('all')}
                                        />
                                    </Tooltip>
                                )}
                                <Tooltip content={t('actions.reply')}>
                                    <Button
                                        color="tertiary-text"
                                        size="small"
                                        icon={<span className="material-icons">reply</span>}
                                        aria-label={t('actions.reply')}
                                        onClick={() => setShowReplyForm('to')}
                                    />
                                </Tooltip>
                                <DropdownMenu
                                    isOpen={isDropdownOpen}
                                    onOpenChange={setIsDropdownOpen}
                                    options={[
                                        {
                                            label: t('actions.forward'),
                                            icon: <span className="material-icons">forward</span>,
                                        },
                                        {
                                            label: hasSiblingMessages ? t('actions.mark_as_unread_from_here') : t('actions.mark_as_unread'),
                                            icon: <span className="material-icons">mark_email_unread</span>,
                                            callback: () => markAsUnreadFrom(message.id)
                                        },
                                    ]}
                                >
                                    <Tooltip content={t('tooltips.more_options')}>
                                        <Button
                                            onClick={() => setIsDropdownOpen(true)}
                                            icon={<span className="material-icons">more_vert</span>}
                                            color="primary-text"
                                            aria-label={t('tooltips.more_options')}
                                            size="small"
                                        />
                                    </Tooltip>
                                </DropdownMenu>
                            </div>
                        </div>
                    </div>
                    <div className="thread-message__header-rows">
                        <div className="thread-message__header-column thread-message__header-column--left">
                            <dl className="thread-message__correspondents">
                                <dt>{t('thread_message.from')}</dt>
                                <dd>{message.sender.email}</dd>
                                <dt>{t('thread_message.to')}</dt>
                                <dd>{message.to.map((recipient) => recipient.email).join(', ')}</dd>
                                {message.cc.length > 0 && (
                                    <>
                                        <dt>{t('thread_message.cc')}</dt>
                                        <dd>{message.cc.map((recipient) => recipient.email).join(', ')}</dd>
                                    </>
                                )}
                            </dl>
                        </div>
                    </div>
                </header>
                <MessageBody
                    rawTextBody={message.textBody[0]?.content as string}
                    rawHtmlBody={message.htmlBody[0]?.content as string}
                />
                <footer className="thread-message__footer">
                    {
                        isLatest && !showReplyForm && (
                            <div className="thread-message__footer-actions">
                                {hasSeveralRecipients && (
                                    <Button
                                        color="primary"
                                        icon={<span className="material-icons">reply_all</span>}
                                        aria-label={t('actions.reply_all')}
                                        onClick={() => setShowReplyForm('all')}
                                    >
                                        {t('actions.reply_all')}
                                    </Button>
                                )}
                                <Button
                                    color={hasSeveralRecipients ? 'secondary' : 'primary'}
                                    icon={<span className="material-icons">reply</span>}
                                    aria-label={t('actions.reply')}
                                    onClick={() => setShowReplyForm('to')}
                                >
                                    {t('actions.reply')}
                                </Button>
                            </div>
                        )
                    }
                    {showReplyForm && <MessageReplyForm
                        replyAll={showReplyForm === 'all'}
                        handleClose={handleCloseReplyForm}
                        message={message}
                    />}
                </footer>
            </section>
        )
    }
);

ThreadMessage.displayName = "ThreadMessage";
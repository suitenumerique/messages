import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, IconSize, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { MessageDeliveryStatusChoices, MessageRecipient } from "@/features/api/gen/models";
import { Banner } from "@/features/ui/components/banner";
import { Badge } from "@/features/ui/components/badge";
import { ContactChip, ContactChipDeliveryStatus } from "@/features/ui/components/contact-chip";
import { DateHelper } from "@/features/utils/date-helper";
import useTrash from "@/features/message/use-trash";
import { ThreadMessageHeaderProps } from "./types";
import ThreadMessageActions from "./thread-message-actions";

const ThreadMessageHeader = ({
    message,
    draftMessage,
    isLatest,
    isFolded,
    canSendMessages,
    hasSeveralRecipients,
    onToggleFold,
    onSetReplyFormMode,
}: ThreadMessageHeaderProps) => {
    const { t, i18n } = useTranslation();
    const { markAsUntrashed } = useTrash();

    // Derived state specific to header
    const isSuspiciousSender = Boolean(message.stmsg_headers?.['sender-auth'] === 'none');

    // Handler for untrash banner action
    const handleMarkAsUntrashed = useCallback(() => {
        markAsUntrashed({ messageIds: [message.id] });
    }, [markAsUntrashed, message.id]);

    const getRecipientDeliveryStatus = useCallback((recipient: MessageRecipient): ContactChipDeliveryStatus | undefined => {
        // If the message has just been sent, it has no delivery status but for the sender it is useful to show that the message is being delivered
        if (message.is_sender && recipient.delivery_status === null && !message.is_draft) {
            return { status: 'delivering', timestamp: null, message: null };
        }
        switch (recipient.delivery_status) {
            case MessageDeliveryStatusChoices.failed:
                return { status: 'undelivered', timestamp: recipient.retry_at, message: recipient.delivery_message };
            case MessageDeliveryStatusChoices.retry:
                return { status: 'delivering', timestamp: recipient.retry_at, message: recipient.delivery_message };
            case MessageDeliveryStatusChoices.sent:
            case MessageDeliveryStatusChoices.internal:
                return { status: 'delivered', timestamp: recipient.delivered_at, message: recipient.delivery_message };
            default:
                return undefined;
        }
    }, [message.is_sender, message.is_draft]);

    return (
        <header className="thread-message__header">
            <button
                className="thread-message__header-toggle"
                onClick={onToggleFold}
                disabled={isLatest}
                aria-hidden={isLatest}
                aria-label={isFolded ? t('Unfold message') : t('Fold message')}
                title={isFolded ? t('Unfold message') : t('Fold message')}
            />
            <div>
                {message.is_trashed && (
                    <Banner
                        type="info"
                        icon={<span className="material-icons">restore_from_trash</span>}
                        fullWidth
                        actions={[
                            {
                                label: t('Undelete'),
                                onClick: handleMarkAsUntrashed,
                            }
                        ]}
                    >
                        <p>{t('This message has been deleted.')}</p>
                    </Banner>
                )}
                <div className="thread-message__header-rows" style={{ marginBottom: 'var(--c--globals--spacings--sm)' }}>
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
                                    <div className="flex-row">
                                        {(message.is_draft || draftMessage) && (
                                            <Tooltip placement="bottom" content={t('This message has a draft')}>
                                                <Badge aria-label={t('Draft')} variant="tertiary" color="neutral">
                                                    <Icon type={IconType.FILLED} name="mode_edit" className="icon--size-sm" />
                                                </Badge>
                                            </Tooltip>
                                        )}
                                        {message.attachments.length > 0 && (
                                            <Tooltip placement="bottom" content={t('This message has {{count}} attachments', { count: message.attachments.length, defaultValue_one: 'This message has one attachment' })}>
                                                <Badge
                                                    aria-label={t('{{count}} attachments', { count: message.attachments.length })}
                                                    color="neutral"
                                                    variant="tertiary"
                                                >
                                                    <Icon type={IconType.FILLED} name="attachment" size={IconSize.SMALL} />
                                                </Badge>
                                            </Tooltip>
                                        )}
                                    </div>
                                    {message.created_at && (
                                        <p className="thread-message__date">
                                            {t('{{date}} at {{time}}', {
                                                date: DateHelper.formatDate(message.created_at, i18n.resolvedLanguage, false),
                                                time: new Date(message.created_at).toLocaleString(i18n.resolvedLanguage, {
                                                    minute: '2-digit',
                                                    hour: '2-digit',
                                                })
                                            })}
                                        </p>
                                    )}
                                </div>
                                <ThreadMessageActions
                                    message={message}
                                    isFolded={isFolded}
                                    isLatest={isLatest}
                                    canSendMessages={canSendMessages}
                                    hasSeveralRecipients={hasSeveralRecipients}
                                    onSetReplyFormMode={onSetReplyFormMode}
                                    onToggleFold={onToggleFold}
                                />
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
    );
};

export default ThreadMessageHeader;

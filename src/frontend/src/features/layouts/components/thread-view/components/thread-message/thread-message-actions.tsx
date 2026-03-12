import { useState, useMemo, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip, useModals } from "@gouvfr-lasuite/cunningham-react";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { getMessagesEmlRetrieveUrl } from "@/features/api/gen/messages/messages";
import { getRequestUrl } from "@/features/api/utils";
import { useMailboxContext } from "@/features/providers/mailbox";
import usePrint from "@/features/message/use-print";
import useRead from "@/features/message/use-read";
import useSplitThread from "@/features/message/use-split-thread";
import useTrash from "@/features/message/use-trash";
import { ThreadMessageActionsProps } from "./types";

const ThreadMessageActions = ({
    message,
    isFolded,
    isLatest,
    canSendMessages,
    hasSeveralRecipients,
    onSetReplyFormMode,
    onToggleFold,
}: ThreadMessageActionsProps) => {
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);

    // Hooks and state specific to actions
    const { unselectThread, selectedThread } = useMailboxContext();
    const { markAsReadAt } = useRead();
    const { markAsTrashed } = useTrash();
    const { splitThread } = useSplitThread();
    const { print } = usePrint();
    const modals = useModals();

    const hasSiblingMessages = useMemo(() => {
        if (!selectedThread) return false;
        return selectedThread?.messages?.length > 1;
    }, [selectedThread]);

    const canSplitThread = useMemo(() => {
        if (!selectedThread || !hasSiblingMessages) return false;
        if (message.is_draft) return false;
        if (selectedThread.user_role !== "editor") return false;
        // Cannot split at the first message
        if (selectedThread.messages[0] === message.id) return false;
        return true;
    }, [selectedThread, hasSiblingMessages, message.id, message.is_draft]);

    // Handlers specific to actions
    const toggleReadStateFrom = useCallback((is_unread: boolean) => {
        if (!selectedThread) return;
        if (is_unread) {
            // Mark as unread from here: subtract 1ms so this message becomes unread
            const readAt = new Date(new Date(message.created_at!).getTime() - 1).toISOString();
            markAsReadAt({ threadIds: [selectedThread.id], readAt, onSuccess: unselectThread });
        } else {
            // Mark as read from here: read up to this message's created_at
            markAsReadAt({ threadIds: [selectedThread.id], readAt: message.created_at! });
        }
    }, [message.id, message.created_at, unselectThread, selectedThread, markAsReadAt]);

    const handleMarkAsTrashed = useCallback(() => {
        markAsTrashed({ messageIds: [message.id] });
    }, [markAsTrashed, message.id]);

    const handleSplitThread = useCallback(async () => {
        if (!selectedThread) return;
        const decision = await modals.confirmationModal({
            titleIcon: <Icon type={IconType.FILLED} name="call_split" />,
            title: <span className="c__modal__text--centered">{t('Split thread')}</span>,
            children: t('This will move this message and all following messages to a new thread. Continue?'),
        });
        if (decision !== 'yes') return;
        splitThread({ threadId: selectedThread.id, messageId: message.id });
    }, [selectedThread, splitThread, message.id, t, modals]);

    const handleDownloadRawEmail = useCallback(() => {
        const downloadUrl = getRequestUrl(getMessagesEmlRetrieveUrl(message.id));
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = `message-${message.id}.eml`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }, [message.id]);

    const dropdownOptions = [
        ...(canSendMessages && hasSeveralRecipients ? [{
            label: t('Reply all'),
            icon: <Icon type={IconType.FILLED} name="reply_all" />,
            callback: () => onSetReplyFormMode('reply_all')
        }] : []),
        ...(canSendMessages ? [{
            label: t('Forward'),
            icon: <Icon type={IconType.FILLED} name="forward" />,
            callback: () => onSetReplyFormMode('forward'),
            showSeparator: true
        }] : []),
        ...(message.is_unread ? [{
            label: hasSiblingMessages ? t('Mark as read from here') : t('Mark as read'),
            icon: <Icon type={IconType.FILLED} name="mark_email_read" />,
            callback: () => toggleReadStateFrom(false)
        }] :
        [{
            label: hasSiblingMessages ? t('Mark as unread from here') : t('Mark as unread'),
            icon: <Icon type={IconType.FILLED} name="mark_email_unread" />,
            callback: () => toggleReadStateFrom(true)
        }]),
        ...(canSplitThread ? [{
            label: t('Split thread from here'),
            icon: <Icon type={IconType.FILLED} name="call_split" />,
            showSeparator: true,
            callback: handleSplitThread,
        }] : []),
        {
            label: t('Print'),
            icon: <Icon type={IconType.FILLED} name="print" />,
            callback: () => print(message)
        },
        {
            label: t('Download raw email'),
            icon: <Icon type={IconType.FILLED} name="download" />,
            callback: handleDownloadRawEmail
        },
        ...(message.is_trashed ? [] : [{
            label: t('Delete'),
            icon: <Icon type={IconType.FILLED} name="delete" />,
            callback: handleMarkAsTrashed
        }]),
    ];

    return (
        <div className="thread-message__header-actions">
            {!isFolded && (
                <>
                    {canSendMessages && (
                        <Tooltip content={t('Reply')}>
                            <Button
                                color="brand"
                                variant="tertiary"
                                size="small"
                                icon={<Icon type={IconType.FILLED} name="reply" />}
                                aria-label={t('Reply')}
                                onClick={() => onSetReplyFormMode('reply')}
                            />
                        </Tooltip>
                    )}
                    <DropdownMenu
                        isOpen={isDropdownOpen}
                        onOpenChange={setIsDropdownOpen}
                        options={dropdownOptions}
                    >
                        <Tooltip content={t('More options')}>
                            <Button
                                onClick={() => setIsDropdownOpen(true)}
                                icon={<Icon type={IconType.FILLED} name="more_vert" />}
                                color="brand"
                                variant="tertiary"
                                aria-label={t('More options')}
                                size="small"
                            />
                        </Tooltip>
                    </DropdownMenu>
                </>
            )}
            {!isLatest && (
                <Tooltip content={isFolded ? t('Unfold message') : t('Fold message')}>
                    <Button
                        color="brand"
                        variant="tertiary"
                        size="small"
                        icon={<Icon type={IconType.FILLED} name={isFolded ? "unfold_more" : "unfold_less"} />}
                        aria-label={isFolded ? t('Unfold message') : t('Fold message')}
                        onClick={onToggleFold}
                    />
                </Tooltip>
            )}
        </div>
    );
};

export default ThreadMessageActions;

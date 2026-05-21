import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { AttachmentList } from "../thread-attachment-list";
import { ThreadMessageFooterProps } from "./types";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";

const ThreadMessageFooter = ({
    message,
    regularAttachments,
    driveAttachments,
    showReplyButton,
    hasSeveralRecipients,
    onSetReplyFormMode,
    intersectionRef,
}: ThreadMessageFooterProps) => {
    const { t } = useTranslation();

    const hasAttachments = !message.is_draft && (regularAttachments.length > 0 || driveAttachments.length > 0);

    return (
        <footer className="thread-message__footer">
            <span
                className="thread-message__intersection-trigger"
                ref={intersectionRef}
                data-message-id={message.id}
                data-created-at={message.created_at}
            />
            {hasAttachments && (
                <AttachmentList attachments={[...regularAttachments, ...driveAttachments]} />
            )}
            {showReplyButton && (
                <div className="thread-message__footer-actions">
                    {hasSeveralRecipients && (
                        <Button
                            color="brand"
                            variant="primary"
                            size="small"
                            icon={<Icon name="reply_all" type={IconType.OUTLINED} />}
                            aria-label={t('Reply all')}
                            onClick={() => onSetReplyFormMode('reply_all')}
                        >
                            {t('Reply all')}
                        </Button>
                    )}
                    <Button
                        variant={hasSeveralRecipients ? 'tertiary' : 'primary'}
                        icon={<Icon name="reply" type={IconType.OUTLINED} />}
                        aria-label={t('Reply')}
                        size="small"
                        onClick={() => onSetReplyFormMode('reply')}
                    >
                        {t('Reply')}
                    </Button>
                    <Button
                        variant='tertiary'
                        size="small"
                        icon={<Icon name="forward" type={IconType.OUTLINED} />}
                        onClick={() => onSetReplyFormMode('forward')}
                    >
                        {t('Forward')}
                    </Button>
                </div>
            )}
        </footer>
    );
};

export default ThreadMessageFooter;

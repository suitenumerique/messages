import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { AttachmentList } from "../thread-attachment-list";
import { CalendarInvite } from "../calendar-invite";
import { ThreadMessageFooterProps } from "./types";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useMailboxContext } from "@/features/providers/mailbox";

const CALENDAR_MIME_TYPES = ["text/calendar", "application/ics"];

const ThreadMessageFooter = ({
    message,
    driveAttachments,
    showReplyButton,
    hasSeveralRecipients,
    onSetReplyFormMode,
    intersectionRef,
}: ThreadMessageFooterProps) => {
    const { t } = useTranslation();
    const { selectedThread } = useMailboxContext();

    // Separate calendar attachments from regular attachments
    // Deduplicate calendar invites by SHA256 hash (Google Calendar sends the same ICS as both inline and attachment)
    const { calendarAttachments, regularAttachments } = useMemo(() => {
        const calendar = message.attachments.filter((att) =>
            CALENDAR_MIME_TYPES.includes(att.type)
        );
        const regular = message.attachments.filter(
            (att) => !CALENDAR_MIME_TYPES.includes(att.type)
        );

        // Deduplicate by SHA256 hash (Google Calendar sends the same ICS as both inline and attachment)
        const seenHashes = new Set<string>();
        const uniqueCalendar = calendar.filter((att) => {
            if (seenHashes.has(att.sha256)) {
                return false;
            }
            seenHashes.add(att.sha256);
            return true;
        });

        return { calendarAttachments: uniqueCalendar, regularAttachments: regular };
    }, [message.attachments]);

    const hasAttachments = !message.is_draft && (regularAttachments.length > 0 || driveAttachments.length > 0);
    const hasCalendarInvites = !message.is_draft && calendarAttachments.length > 0;

    return (
        <footer className="thread-message__footer">
            <span
                className="thread-message__intersection-trigger"
                ref={intersectionRef}
                data-message-id={message.id}
            />
            {hasCalendarInvites && (
                <div className="thread-message__calendar-invites">
                    {calendarAttachments.map((attachment) => (
                        <CalendarInvite
                            key={attachment.blobId}
                            attachment={attachment}
                            canDownload={!selectedThread?.is_spam}
                        />
                    ))}
                </div>
            )}
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

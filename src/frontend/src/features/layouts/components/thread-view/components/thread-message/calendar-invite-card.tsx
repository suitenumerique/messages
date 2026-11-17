import { Attachment } from "@/features/api/gen/models";
import ErrorBoundary from "@/features/errors/error-boundary";
import { CalendarInvite } from "../calendar-invite";
import { AttachmentList } from "../thread-attachment-list";
import { SpamReport } from "./types";

type CalendarInviteCardProps = {
    attachment: Attachment;
    spamReport: SpamReport | null;
    mailboxId?: string;
    mailboxEmail?: string;
};

/**
 * The invite card of a calendar attachment. The invite is sender-controlled:
 * if it still breaks rendering, the file is shown as a plain attachment (the
 * message footer leaves calendar files out) rather than losing the whole
 * message view.
 */
export const CalendarInviteCard = ({ attachment, spamReport, mailboxId, mailboxEmail }: CalendarInviteCardProps) => (
    <ErrorBoundary fallback={<AttachmentList attachments={[attachment]} spamReport={spamReport} />}>
        <CalendarInvite
            attachment={attachment}
            canDownload={!spamReport}
            mailboxId={mailboxId}
            mailboxEmail={mailboxEmail}
        />
    </ErrorBoundary>
);

import { Attachment, Message } from "@/features/api/gen/models";
import { MessageFormMode } from "@/features/forms/components/message-form";
import { BodyPart } from "./renderers";

export type ThreadMessageProps = {
    message: Message;
    isLatest: boolean;
    draftMessage?: Message;
} & React.HTMLAttributes<HTMLElement>;

export type ThreadMessageHeaderProps = {
    message: Message;
    draftMessage?: Message;
    isLatest: boolean;
    isFolded: boolean;
    canSendMessages: boolean;
    canRetry: boolean;
    hasSeveralRecipients: boolean;
    onToggleFold: () => void;
    onSetReplyFormMode: (mode: MessageFormMode) => void;
    onUpdateRecipientStatus?: (recipientId: string, status: 'cancelled' | 'retry') => void;
};

export type ThreadMessageActionsProps = {
    message: Message;
    isFolded: boolean;
    isLatest: boolean;
    canSendMessages: boolean;
    hasSeveralRecipients: boolean;
    onSetReplyFormMode: (mode: MessageFormMode) => void;
    onToggleFold: () => void;
};

export type ThreadMessageBodyProps = {
    /** Array of body parts to render (from htmlBody or textBody) */
    bodyParts: readonly BodyPart[];
    attachments?: readonly Attachment[];
    messageId: string;
    isHidden?: boolean;
    onLoad?: () => void;
}

export type ThreadMessageFooterProps = {
    message: Message;
    regularAttachments: Attachment[];
    driveAttachments: ReturnType<typeof import("@/features/utils/mail-helper").default.extractDriveAttachmentsFromHtmlBody>[1];
    showReplyButton: boolean;
    hasSeveralRecipients: boolean;
    onSetReplyFormMode: (mode: MessageFormMode) => void;
    intersectionRef: React.Ref<HTMLSpanElement>;
};

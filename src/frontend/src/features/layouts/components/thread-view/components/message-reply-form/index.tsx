import { useRef, useState } from "react";
import { Message } from "@/features/api/gen";
import { MessageForm, MessageFormHandle, MessageFormMode } from "@/features/forms/components/message-form";
import { useQueryClient } from "@tanstack/react-query";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { DateHelper } from "@/features/utils/date-helper";
import DraftActionsMenu from "./draft-actions-menu";

type MessageReplyFormProps = {
    handleClose: () => void;
    mode?: MessageFormMode;
    message: Message;
    // When the draft replies to an existing message, render it as a distinct,
    // detached compose surface (labelled header, accent rail, elevation) so it
    // no longer reads as nested inside the message it answers.
    detached?: boolean;
};

const MessageReplyForm = ({ handleClose, message, mode, detached = false }: MessageReplyFormProps) => {
    const { t, i18n } = useTranslation();
    const queryClient = useQueryClient();
    const formRef = useRef<MessageFormHandle>(null);
    // Sourced from MessageForm so the menu mirrors the exact mailbox + thread
    // permission check that gates the in-form delete button.
    const [canDelete, setCanDelete] = useState(false);

    return (
        <div
            className={clsx("message-reply-form-container", {
                "message-reply-form-container--detached": detached,
            })}
        >
            {detached && (
                <div className="message-reply-form-container__header">
                    <Icon name="edit" type={IconType.OUTLINED} />
                    <span className="message-reply-form-container__header-label">
                        {t("Draft")}
                    </span>
                    {message.is_draft && message.created_at && (
                        <span className="message-reply-form-container__header-date">
                            {DateHelper.formatDate(message.created_at, i18n.resolvedLanguage)}
                        </span>
                    )}
                    {message.is_draft && (
                        <div className="message-reply-form-container__header-actions">
                            <DraftActionsMenu
                                message={message}
                                onDelete={canDelete ? () => formRef.current?.deleteDraft() : undefined}
                            />
                        </div>
                    )}
                </div>
            )}
            <div className="message-reply-form-container__body">
                <MessageForm
                    ref={formRef}
                    draftMessage={message.is_draft ? message : undefined}
                    parentMessage={message.is_draft ? undefined : message}
                    mode={mode}
                    onDeletableChange={setCanDelete}
                    onSuccess={() => {
                        // Close right away: MessageForm has optimistically un-drafted
                        // the message, so the thread already shows it as sending.
                        handleClose();
                        // Reconcile with the server state (delivery status, etc.) in
                        // the background without blocking the form close.
                        void queryClient.refetchQueries({ queryKey: ["messages", message.thread_id] });
                    }}
                    onClose={handleClose}
                />
            </div>
        </div>
    );
};

export default MessageReplyForm;

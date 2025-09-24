import { Message } from "@/features/api/gen";
import { MessageForm, MessageFormMode } from "@/features/forms/components/message-form";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useQueryClient } from "@tanstack/react-query";

type MessageReplyFormProps = {
    handleClose: () => void;
    mode?: MessageFormMode;
    message: Message;
};

const MessageReplyForm = ({ handleClose, message, mode }: MessageReplyFormProps) => {
    const queryClient = useQueryClient();
    const { unselectThread } = useMailboxContext();

    return (
        <div className="message-reply-form-container">
            <MessageForm
                draftMessage={message.is_draft ? message : undefined}
                parentMessage={message.is_draft ? undefined : message}
                mode={mode}
                onSuccess={() => {
                    // Remove the message query cache to avoid showing the draft message in the thread view
                    handleClose();
                    unselectThread();
                    queryClient.removeQueries({ queryKey: ["messages", message.thread_id] });
                }}
                onClose={message.is_draft ? undefined : handleClose}
            />
        </div>
    );
};

export default MessageReplyForm;

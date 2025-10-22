import { Message } from "@/features/api/gen";
import { MessageForm, MessageFormMode } from "@/features/forms/components/message-form";
import { useQueryClient } from "@tanstack/react-query";

type MessageReplyFormProps = {
    handleClose: () => void;
    mode?: MessageFormMode;
    message: Message;
};

const MessageReplyForm = ({ handleClose, message, mode }: MessageReplyFormProps) => {
    const queryClient = useQueryClient();

    return (
        <div className="message-reply-form-container">
            <MessageForm
                draftMessage={message.is_draft ? message : undefined}
                parentMessage={message.is_draft ? undefined : message}
                mode={mode}
                onSuccess={async () => {
                    // Force refetch the messages query to avoid showing the draft message in the thread view
                    await queryClient.refetchQueries({ queryKey: ["messages", message.thread_id] });
                    handleClose();
                }}
                onClose={handleClose}
            />
        </div>
    );
};

export default MessageReplyForm;

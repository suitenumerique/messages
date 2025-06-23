import { Message } from "@/features/api/gen";
import { MessageForm } from "@/features/forms/components/message-form";

type MessageReplyFormProps = {
    handleClose: () => void;
    replyAll: boolean;
    message: Message;
};

const MessageReplyForm = ({ handleClose, message, replyAll }: MessageReplyFormProps) => {
    return (
        <div className="message-reply-form-container">
            <MessageForm
                draftMessage={message.is_draft ? message : undefined}
                parentMessage={message.is_draft ? undefined : message}
                replyAll={replyAll}
                onSuccess={handleClose}
                onClose={message.is_draft ? undefined : handleClose}
            />
        </div>
    );
};

export default MessageReplyForm;

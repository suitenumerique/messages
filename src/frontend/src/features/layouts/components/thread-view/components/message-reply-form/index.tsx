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
                parentMessage={message}
                replyAll={replyAll}
                onClose={handleClose}
            />
        </div>
    );
};

export default MessageReplyForm;
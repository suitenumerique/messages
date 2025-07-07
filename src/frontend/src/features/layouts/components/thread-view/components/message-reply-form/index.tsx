import { Message } from "@/features/api/gen";
import { MessageForm, MessageFormMode } from "@/features/forms/components/message-form";

type MessageReplyFormProps = {
    handleClose: () => void;
    mode?: MessageFormMode;
    message: Message;
};

const MessageReplyForm = ({ handleClose, message, mode }: MessageReplyFormProps) => {
    return (
        <div className="message-reply-form-container">
            <MessageForm
                draftMessage={message.is_draft ? message : undefined}
                parentMessage={message.is_draft ? undefined : message}
                mode={mode}
                onSuccess={handleClose}
                onClose={message.is_draft ? undefined : handleClose}
            />
        </div>
    );
};

export default MessageReplyForm;

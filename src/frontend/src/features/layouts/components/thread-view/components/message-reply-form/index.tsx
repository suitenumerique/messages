import { Message } from "@/features/api/gen";
import { MessageForm } from "@/features/forms/components/message-form";
import { DEFAULT_FOLDERS } from "../../../mailbox-panel/components/mailbox-list";
import { usePathname, useRouter } from "next/navigation";

type MessageReplyFormProps = {
    handleClose: () => void;
    replyAll: boolean;
    message: Message;
};

const MessageReplyForm = ({ handleClose, message, replyAll }: MessageReplyFormProps) => {
    const router = useRouter()
    const pathname = usePathname();
    const goToDefaultFolder = () => {
        const defaultFolder = DEFAULT_FOLDERS[0];
        router.push(pathname + `?${new URLSearchParams(defaultFolder.filter).toString()}`);
    }

    return (
        <div className="message-reply-form-container">
            <MessageForm
                draftMessage={message.is_draft ? message : undefined}
                parentMessage={message.is_draft ? undefined : message}
                showSubject={message.is_draft}
                showMailboxes={message.is_draft}
                replyAll={replyAll}
                onSuccess={goToDefaultFolder}
                onClose={message.is_draft ? undefined : handleClose}
            />
        </div>
    );
};

export default MessageReplyForm;
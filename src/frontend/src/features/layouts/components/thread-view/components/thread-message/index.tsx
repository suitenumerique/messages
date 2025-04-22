import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Message } from "@/features/api/gen/models";
import MessageBody from "./message-body"
import MessageReplyForm from "../message-reply-form";

type ThreadMessageProps = {
    message: Message
    showReplyAllForm: boolean
    resetReplyAllForm: () => void
}

export const ThreadMessage = ({ message, showReplyAllForm, resetReplyAllForm }: ThreadMessageProps) => {
    const { t, i18n } = useTranslation()
    const [showReplyForm, setShowReplyForm] = useState(false)

    const handleCloseReplyForm = () => {
        setShowReplyForm(false);
        resetReplyAllForm();
    }

    useEffect(() => {
        if (showReplyAllForm) {
            setShowReplyForm(true);
        }
    }, [showReplyAllForm]);

    return (
        <section className="thread-message" data-read={Boolean(message.read_at)}>
            <header className="thread-message__header">
                <div className="thread-message__header-rows">
                    <div className="thread-message__header-column thread-message__header-column--left">
                        <h2 className="thread-message__subject">{message.subject}</h2>
                    </div>
                    <div className=" thread-message__header-column thread-message__header-column--right flex-row flex-align-center">
                        <p className="thread-message__date m-0">{new Date(message.received_at).toLocaleString(i18n.language, {
                            minute: '2-digit',
                            hour: '2-digit',
                            day: '2-digit',
                            month: '2-digit',
                            year: 'numeric',
                        })}</p>
                    </div> 
                </div>
                <div className="thread-message__header-rows">
                    <div className="thread-message__header-column thread-message__header-column--left">
                        <dl className="thread-message__correspondents">
                            <dt>{t('thread_message.from')}</dt>
                            <dd>{message.sender.email}</dd>
                            <dt>{t('thread_message.to')}</dt>
                            <dd>{message.to.map((recipient) => recipient.email).join(', ')}</dd>
                            {message.cc.length > 0 && (
                                <>
                                    <dt>{t('thread_message.cc')}</dt>
                                    <dd>{message.cc.map((recipient) => recipient.email).join(', ')}</dd>
                                </>
                            )}
                        </dl>
                    </div>
                </div>
            </header>
            <MessageBody
                rawTextBody={message.textBody[0]?.content as string}
                rawHtmlBody={message.htmlBody[0]?.content as string}
            />
            {showReplyForm && <MessageReplyForm 
                handleClose={handleCloseReplyForm}
                message={message}
                replyAll={showReplyAllForm}
            />}
        </section>
    )
}
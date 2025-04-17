import { useTranslation } from "react-i18next";
import MessageBody from "./message-body"
import { Message } from "@/features/api/gen/models";

type ThreadMessageProps = {
    message: Message
}

export const ThreadMessage = ({ message }: ThreadMessageProps) => {
    const { t, i18n } = useTranslation()
    return (
        <section className="thread-message" data-read={message.is_read}>
            <header className="thread-message__header">
                <div className="thread-message__header-row">
                    <div className="thread-message__header-row-left">
                        <h2 className="thread-message__subject">{message.subject}</h2>
                    </div>
                    <div className="thread-message__header-row-right">
                        <p className="thread-message__date">{new Date(message.received_at).toLocaleString(i18n.language, {
                            minute: '2-digit',
                            hour: '2-digit',
                            day: '2-digit',
                            month: '2-digit',
                            year: 'numeric',
                        })}</p>
                    </div> 
                </div>
                <div className="thread-message__header-row">
                    <div className="thread-message__header-row-left">
                        <dl className="thread-message__correspondents">
                            <dt>{t('thread_message.from')}</dt>
                            <dd>{message.sender.email}</dd>
                            <dt>{t('thread_message.to')}</dt>
                            <dd>{message.recipients.map((recipient) => recipient.contact.email).join(', ')}</dd>
                        </dl>
                    </div>
                </div>
            </header>
            <MessageBody
                rawTextBody={message.raw_text_body}
                rawHtmlBody={message.raw_html_body}
            />
        </section>
    )
}
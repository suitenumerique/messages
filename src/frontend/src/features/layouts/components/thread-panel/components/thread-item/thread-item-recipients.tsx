import { Contact } from "@/features/api/gen/models"

type ThreadItemRecipientsProps = {
    recipients: Contact[]
}

export const ThreadItemRecipients = ({ recipients }: ThreadItemRecipientsProps) => {
    return (
        <div className="thread-item__recipients-container">
            <ul className="thread-item__recipients">
                {recipients.map((recipient) => (
                    <li className="thread-item__recipient" key={recipient.id}>
                        <strong>{recipient.name || recipient.email}</strong>
                    </li>
                ))}
            </ul>
            {recipients.length > 1 && (
                <span className="thread-item__recipients-count">
                    {recipients.length}
                </span>
            )}
        </div>
    )
}
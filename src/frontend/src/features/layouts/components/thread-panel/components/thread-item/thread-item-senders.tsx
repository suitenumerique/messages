type ThreadItemSendersProps = {
    senders: readonly string[],
    isUnread: boolean
    messagesCount: number
}

export const ThreadItemSenders = ({ senders, messagesCount, isUnread }: ThreadItemSendersProps) => {
    const [initialSender, lastSender] = senders;

    return (
        <div className="thread-item__senders-container">
            <ul className="thread-item__senders">
                <li className="thread-item__sender">
                    {!isUnread || lastSender ? initialSender : <strong>{initialSender}</strong>}
                </li>
                {lastSender && (
                    <li className="thread-item__sender">
                        {isUnread ? <strong>{lastSender}</strong> : lastSender}
                    </li>
                )}
            </ul>
            {messagesCount > 1 && (
                <span className="thread-item__messages-count">
                    {messagesCount}
                </span>
            )}
        </div>
    )
}

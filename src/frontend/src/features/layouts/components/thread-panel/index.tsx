import { ThreadItem } from "./components/thread-item";

// TODO: Use real data
const TMP_THREADS = [...Array(100)].map((_, index) => ({
    id: index.toString(),
    has_attachments:  Math.random() > 0.5,
    is_unread: Math.random() > 0.5,
    is_important:  Math.random() > 0.5,
    sent_at: index === 0 ? "2025-04-15T00:00:00Z" : new Date(Date.now() - ((1000 * 60 * 60 * 24 * 182)/10) * index).toISOString(),
    sender_name: "John Doe",
    subject: "Subject "+index,
}));

export const ThreadPanel = () => {
    return (
        <div className="thread-panel">
            {
                TMP_THREADS.map((thread) => (
                    <ThreadItem key={thread.id} thread={thread} />
                ))
            }
        </div>
    )
}
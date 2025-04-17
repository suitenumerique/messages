import { useMailboxContext } from "@/features/mailbox/provider";
import { ThreadItem } from "./components/thread-item";


export const ThreadPanel = () => {
    const { threads } = useMailboxContext();
    return (
        <div className="thread-panel">
            {
                threads?.results.map((thread) => (
                    <ThreadItem key={thread.id} thread={thread} />
                ))
            }
        </div>
    )
}
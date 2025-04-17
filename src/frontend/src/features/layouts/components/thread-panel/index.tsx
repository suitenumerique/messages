import { useMailboxContext } from "@/features/mailbox/provider";
import { ThreadItem } from "./components/thread-item";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";

export const ThreadPanel = () => {
    const { threads, status } = useMailboxContext();
    const { t } = useTranslation();

    if (status.threads === "pending") {
        return (
            <div className="thread-panel thread-panel--loading">
                <Spinner />
            </div>
        );
    }

    if (!threads?.results.length) {
        return (
            <div className="thread-panel thread-panel--empty">
                <div>
                    <span className="material-icons">mail</span>
                    <p>{t('no_threads')}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="thread-panel">
            {threads.results.map((thread) => (
                <ThreadItem key={thread.id} thread={thread} />
            ))}
        </div>
    );
}
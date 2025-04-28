import { useMailboxContext } from "@/features/mailbox/provider";
import { ThreadItem } from "./components/thread-item";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import Bar from "@/features/ui/components/bar";
import { Button, Tooltip } from "@openfun/cunningham-react";

export const ThreadPanel = () => {
    const { threads, status, refetchMailboxes } = useMailboxContext();
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
            <Bar>
                <Tooltip content={t('actions.refresh')}>
                    <Button
                        onClick={refetchMailboxes}
                        icon={<span className="material-icons">refresh</span>}
                        color="tertiary-text"
                        size="small"
                        aria-label={t('actions.refresh')}
                    />
                </Tooltip>
            </Bar>
            {threads.results.map((thread) => (
                <ThreadItem key={thread.id} thread={thread} />
            ))}
        </div>
    );
}
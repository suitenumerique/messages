import { useMailboxContext } from "@/features/mailbox/provider";
import { ThreadItem } from "./components/thread-item";
import { DropdownMenu, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import Bar from "@/features/ui/components/bar";
import { Button, Tooltip } from "@openfun/cunningham-react";
import useRead from "@/features/message/useRead";
import { useState } from "react";

export const ThreadPanel = () => {
    const { threads, status, refetchMailboxes, unselectThread } = useMailboxContext();
    const { markAsRead, markAsUnread } = useRead();
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);

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
                <Tooltip content={t('actions.mark_all_as_read')}>
                    <Button
                        onClick={() => markAsRead({ threadIds: threads.results.map((thread) => thread.id) })}
                        icon={<span className="material-icons">mark_email_read</span>}
                        color="tertiary-text"
                        size="small"
                        aria-label={t('actions.mark_all_as_read')}
                    />
                </Tooltip>
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        {
                            label: t('actions.mark_all_as_unread'),
                            icon: <span className="material-icons">mark_email_unread</span>,
                            callback: () => {
                                markAsUnread({
                                    threadIds: threads.results.map((thread) => thread.id),
                                    onSuccess: unselectThread
                                })
                            },
                        },
                    ]}
                >
                    <Tooltip content={t('tooltips.more_options')}>
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<span className="material-icons">more_vert</span>}
                            color="primary-text"
                            aria-label={t('tooltips.more_options')}
                            size="small"
                        />
                    </Tooltip>
                </DropdownMenu>
            </Bar>
            {threads.results.map((thread) => (
                <ThreadItem key={thread.id} thread={thread} />
            ))}
        </div>
    );
}
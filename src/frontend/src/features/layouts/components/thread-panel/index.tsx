import { useMailboxContext } from "@/features/mailbox/provider";
import { ThreadItem } from "./components/thread-item";
import { DropdownMenu, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import Bar from "@/features/ui/components/bar";
import { Button, Tooltip } from "@openfun/cunningham-react";
import useRead from "@/features/message/use-read";
import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "next/navigation";

export const ThreadPanel = () => {
    const { threads, queryStates, refetchMailboxes, unselectThread, loadNextThreads, selectedThread } = useMailboxContext();
    const { markAsRead, markAsUnread } = useRead();
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const loaderRef = useRef<HTMLDivElement>(null);
    const searchParams = useSearchParams();
    const hideDrafts = !(searchParams.get('has_draft') === '1') || searchParams.get('has_trashed') === '1';
    const hideSend = hideDrafts && !(searchParams.get('has_sender') === '1');
    const filteredThreads = threads?.results.filter((thread) =>
        !(hideDrafts && thread.count_messages === 1 && thread.count_draft === 1)
        || !(hideSend && thread.count_messages === 1 && thread.count_sender === 1)
    ) ?? [];

    const handleObserver = useCallback((entries: IntersectionObserverEntry[]) => {
        const target = entries[0];
        if (target.isIntersecting && threads?.next && !queryStates.threads.isFetchingNextPage) {
            loadNextThreads()
        }
    }, [threads?.next, loadNextThreads, queryStates.threads.isFetchingNextPage]);

    useEffect(() => {
        const observer = new IntersectionObserver(handleObserver, {
            root: null,
            rootMargin: "20px",
            threshold: 0.1,
        });

        if (loaderRef.current) {
            observer.observe(loaderRef.current);
        }

        return () => observer.disconnect();
    }, [handleObserver]);

    useEffect(() => {
        if (selectedThread && !filteredThreads.find((thread) => thread.id === selectedThread.id)) {
            unselectThread();
        }
    }, [filteredThreads, selectedThread, unselectThread]);

    if (queryStates.threads.isLoading) {
        return (
            <div className="thread-panel thread-panel--loading">
                <Spinner />
            </div>
        );
    }

    if (!filteredThreads.length) {
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
            <Bar className="thread-panel__bar">
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
                        onClick={() => markAsRead({ threadIds: filteredThreads.map((thread) => thread.id) })}
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
                                    threadIds: filteredThreads.map((thread) => thread.id),
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
            <div className="thread-panel__threads_list">
                {filteredThreads.map((thread) => <ThreadItem key={thread.id} thread={thread} />)}
                {threads!.next && (
                    <div className="thread-panel__page-loader" ref={loaderRef}>
                        {queryStates.threads.isFetchingNextPage && (
                            <>
                                <Spinner />
                                <span>Loading next threads...</span>
                            </>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
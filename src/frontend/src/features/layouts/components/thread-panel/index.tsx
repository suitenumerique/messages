import { useMailboxContext } from "@/features/providers/mailbox";
import { ThreadItem } from "./components/thread-item";
import { DropdownMenu, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import Bar from "@/features/ui/components/bar";
import { Button, Tooltip } from "@openfun/cunningham-react";
import useRead from "@/features/message/use-read";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { MAILBOX_FOLDERS } from "../mailbox-panel/components/mailbox-list";
import Image from "next/image";

export const ThreadPanel = () => {
    const { threads, queryStates, refetchMailboxes, unselectThread, loadNextThreads, selectedThread } = useMailboxContext();
    const { markAsRead, markAsUnread } = useRead();
    const searchParams = useSearchParams();
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const loaderRef = useRef<HTMLDivElement>(null);
    const showImportButton = useMemo(() => {
        // Only show import button if there are no threads in inbox or all messages folders
        if (threads?.results.length) return false;
        const importableMessageFolders = MAILBOX_FOLDERS.filter((folder) => ['inbox', 'all_messages'].includes(folder.id));
        return importableMessageFolders.some((folder) => searchParams.toString() === new URLSearchParams(folder.filter).toString());
    }, [threads?.results, searchParams]);

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
        if (selectedThread && !threads?.results.find((thread) => thread.id === selectedThread.id)) {
            unselectThread();
        }
    }, [threads?.results, selectedThread, unselectThread]);

    if (queryStates.threads.isLoading) {
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
                    <Image src="/images/svg/read-mail.svg" alt="" width={60} height={60} />
                    <p>{t('No threads.')}</p>
                    {showImportButton && (
                        <Button href="#modal-message-importer">{t('Import messages')}</Button>
                    )}
                </div>
            </div>
        );
    }

    return (
        <div className="thread-panel">
            <Bar className="thread-panel__bar">
                <Tooltip content={t('Refresh')}>
                    <Button
                        onClick={refetchMailboxes}
                        icon={<span className="material-icons">refresh</span>}
                        color="tertiary-text"
                        size="small"
                        aria-label={t('Refresh')}
                    />
                </Tooltip>
                <Tooltip content={t('Mark all as read')}>
                    <Button
                        onClick={() => markAsRead({ threadIds: threads?.results.map((thread) => thread.id) })}
                        icon={<span className="material-icons">mark_email_read</span>}
                        color="tertiary-text"
                        size="small"
                        aria-label={t('Mark all as read')}
                    />
                </Tooltip>
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        {
                            label: t('Mark all as unread'),
                            icon: <span className="material-icons">mark_email_unread</span>,
                            callback: () => {
                                markAsUnread({
                                    threadIds: threads?.results.map((thread) => thread.id),
                                    onSuccess: unselectThread
                                })
                            },
                        },
                    ]}
                >
                    <Tooltip content={t('More options')}>
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<span className="material-icons">more_vert</span>}
                            color="primary-text"
                            aria-label={t('More options')}
                            size="small"
                        />
                    </Tooltip>
                </DropdownMenu>
            </Bar>
            <div className="thread-panel__threads_list">
                {threads?.results.map((thread) => <ThreadItem key={thread.id} thread={thread} />)}
                {threads!.next && (
                    <div className="thread-panel__page-loader" ref={loaderRef}>
                        {queryStates.threads.isFetchingNextPage && (
                            <>
                                <Spinner />
                                <span>{t('Loading next threads...')}</span>
                            </>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

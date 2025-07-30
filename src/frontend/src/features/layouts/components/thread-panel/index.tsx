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

export const ThreadPanel = () => {
    const { threads, queryStates, refetchMailboxes, unselectThread, loadNextThreads, selectedThread, error } = useMailboxContext();
    const { markAsRead, markAsUnread } = useRead();
    const searchParams = useSearchParams();
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const loaderRef = useRef<HTMLDivElement>(null);
    
    // Debug: Log thread panel state when message_ids is present
    useEffect(() => {
        if (searchParams.get('message_ids')) {
            console.log('Thread Panel: Detected message_ids parameter:', searchParams.get('message_ids'));
            console.log('Thread Panel: Threads data:', {
                threads_count: threads?.results.length || 0,
                threads_total: threads?.count || 0,
                query_status: queryStates.threads.status,
                is_loading: queryStates.threads.isLoading,
                is_fetching: queryStates.threads.isFetching,
                has_error: !!error.threads,
                raw_threads_object: threads
            });
            if (threads?.results.length) {
                console.log('Thread Panel: First thread details:', {
                    id: threads.results[0].id,
                    subject: threads.results[0].subject,
                    sender_names: threads.results[0].sender_names
                });
            } else {
                console.log('Thread Panel: No threads found despite API success - this is the bug!');
            }
        }
    }, [searchParams, threads, queryStates.threads, error.threads]);
    
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
        // Debug: Log why we're showing empty state when message_ids is present
        if (searchParams.get('message_ids')) {
            console.log('Thread Panel: Showing empty state despite message_ids parameter:', {
                message_ids: searchParams.get('message_ids'),
                threads_object: threads,
                threads_results_length: threads?.results?.length,
                query_status: queryStates.threads.status,
                is_loading: queryStates.threads.isLoading
            });
        }
        
        return (
            <div className="thread-panel thread-panel--empty">
                <div>
                    <span className="material-icons">mail</span>
                    <p>{t('no_threads')}</p>
                    {showImportButton && (
                        <Button href="#modal-message-importer">{t('actions.import_messages')}</Button>
                    )}
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
                        onClick={() => markAsRead({ threadIds: threads?.results.map((thread) => thread.id) })}
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
                                    threadIds: threads?.results.map((thread) => thread.id),
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
                {threads?.results.map((thread) => {
                    // Debug: Log each thread being rendered when message_ids is present
                    if (searchParams.get('message_ids')) {
                        console.log('Thread Panel: Rendering thread:', {
                            id: thread.id,
                            subject: thread.subject,
                            sender_names: thread.sender_names
                        });
                    }
                    return <ThreadItem key={thread.id} thread={thread} />;
                })}
                {threads!.next && (
                    <div className="thread-panel__page-loader" ref={loaderRef}>
                        {queryStates.threads.isFetchingNextPage && (
                            <>
                                <Spinner />
                                <span>{t('thread-panel.loading-next-threads')}</span>
                            </>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

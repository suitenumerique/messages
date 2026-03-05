import { useMailboxContext } from "@/features/providers/mailbox";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { ThreadItem } from "./components/thread-item";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useEffect, useRef, useCallback, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { MAILBOX_FOLDERS } from "../mailbox-panel/components/mailbox-list";
import Image from "next/image";
import useAbility, { Abilities } from "@/hooks/use-ability";
import ThreadPanelHeader from "./components/thread-panel-header";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { useScrollRestore } from "@/features/providers/scroll-restore";

export const ThreadPanel = () => {
    const { threads, queryStates, unselectThread, loadNextThreads, selectedThread, selectedMailbox } = useMailboxContext();
    const searchParams = useSearchParams();
    const isSearch = searchParams.has('search');
    const { t } = useTranslation();
    const loaderRef = useRef<HTMLDivElement>(null);
    const canImportMessages = useAbility(Abilities.CAN_IMPORT_MESSAGES, selectedMailbox);
    const scrollContextKey = `${selectedMailbox?.id}:${searchParams.toString()}`;
    const { containerRef: scrollContainerRef, onScroll: handleScroll } = useScrollRestore(
        'thread-list', scrollContextKey, [threads],
    );

    const {
        selectedThreadIds,
        isSelectionMode,
        toggleThreadSelection,
        selectAllThreads,
        clearSelection,
        enableSelectionMode,
        isAllSelected,
        isSomeSelected,
    } = useThreadSelection();

    const showImportButton = useMemo(() => {
        // Only show import button if there are no threads in inbox or all messages folders and user has ability to import messages
        if (!canImportMessages) return false;
        if (threads?.results.length) return false;
        const importableMessageFolders = MAILBOX_FOLDERS().filter((folder) => ['inbox', 'all_messages'].includes(folder.id));
        return importableMessageFolders.some((folder) => searchParams.toString() === new URLSearchParams(folder.filter).toString());
    }, [canImportMessages, threads?.results, searchParams]);

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
                    <p>{isSearch ? t('No results.') : t('No threads.')}</p>
                    {showImportButton && (
                        <Button href="#modal-message-importer">{t('Import messages')}</Button>
                    )}
                </div>
            </div>
        );
    }

    return (
        <div id={!selectedThread ? SKIP_LINK_TARGET_ID : undefined} className="thread-panel" tabIndex={-1}>
            <ThreadPanelHeader
                selectedThreadIds={selectedThreadIds}
                isAllSelected={isAllSelected}
                isSomeSelected={isSomeSelected}
                isSelectionMode={isSelectionMode}
                onSelectAll={selectAllThreads}
                onClearSelection={clearSelection}
                onEnableSelectionMode={enableSelectionMode}
                onDisableSelectionMode={clearSelection}
            />
            <div className="thread-panel__threads_list" ref={scrollContainerRef} onScroll={handleScroll}>
                {threads?.results.map((thread) => (
                    <ThreadItem
                        key={thread.id}
                        thread={thread}
                        isSelected={selectedThreadIds.has(thread.id)}
                        onToggleSelection={toggleThreadSelection}
                        selectedThreadIds={selectedThreadIds}
                        isSelectionMode={isSelectionMode}
                    />
                ))}
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

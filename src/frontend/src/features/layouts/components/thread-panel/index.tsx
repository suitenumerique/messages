import { useMailboxContext } from "@/features/providers/mailbox";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { ThreadItem } from "./components/thread-item";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import ThreadPanelHeader from "./components/thread-panel-header";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { useScrollRestore } from "@/features/providers/scroll-restore";
import { useThreadPanelFilters } from "./hooks/use-thread-panel-filters";

export const ThreadPanel = () => {
    const { threads, queryStates, unselectThread, loadNextThreads, selectedThread, selectedMailbox } = useMailboxContext();
    const searchParams = useSearchParams();
    const isSearch = searchParams.has('search');
    const { hasActiveFilters, clearFilters } = useThreadPanelFilters();
    const { t } = useTranslation();
    const loaderRef = useRef<HTMLDivElement>(null);
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
        selectionReadStatus,
        selectionStarredStatus,
    } = useThreadSelection();

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

    const isEmpty = !threads?.results.length;

    return (
        <div id={!selectedThread ? SKIP_LINK_TARGET_ID : undefined} className="thread-panel" tabIndex={-1}>
            <ThreadPanelHeader
                selectedThreadIds={selectedThreadIds}
                isAllSelected={isAllSelected}
                isSomeSelected={isSomeSelected}
                isSelectionMode={isSelectionMode}
                selectionReadStatus={selectionReadStatus}
                selectionStarredStatus={selectionStarredStatus}
                onSelectAll={selectAllThreads}
                onClearSelection={clearSelection}
                onEnableSelectionMode={enableSelectionMode}
                onDisableSelectionMode={clearSelection}
            />
            {isEmpty ? (
                <div className="thread-panel__empty">
                    <div>
                        <p>{hasActiveFilters ? t('No threads match the active filters') : isSearch ? t('No results') : t('No threads')}</p>
                        {hasActiveFilters && (
                            <Button onClick={clearFilters} size="small" variant="secondary">{t('Clear filters')}</Button>
                        )}
                    </div>
                </div>
            ) : (
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
            )}
        </div>
    );
}

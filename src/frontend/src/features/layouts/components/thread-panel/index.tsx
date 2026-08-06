import { useMailboxContext } from "@/features/providers/mailbox";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { ThreadItem } from "./components/thread-item";
import { ThreadItemSwipe } from "./components/thread-item/thread-item-swipe";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useEffect, useRef, useCallback } from "react";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import ThreadPanelHeader from "./components/thread-panel-header";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { useScrollRestore } from "@/features/providers/scroll-restore";
import { useThreadPanelFilters } from "./hooks/use-thread-panel-filters";
import { useThreadListbox } from "./hooks/use-thread-listbox";
import { isNativePlatform } from "@/features/native/platform";
import { usePullToRefresh } from "@/features/native/use-pull-to-refresh";
import { PullToRefreshIndicator } from "@/features/native/pull-to-refresh-indicator";
import { useRefreshFeedback } from "@/hooks/use-refresh-feedback";
import { closeSwipedRows } from "@/hooks/use-swipe-actions";
import { useThreadRowActions } from "@/features/message/use-thread-row-actions";

const PULL_TO_REFRESH_THRESHOLD = 70;

export const ThreadPanel = () => {
    const { threads, queryStates, unselectThread, loadNextThreads, selectedThread, selectedMailbox, invalidateMailbox } = useMailboxContext();
    const searchParams = useUrlSearchParams();
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
        toggleThread,
        selectRange,
        selectAllThreads,
        deselectAllThreads,
        clearSelection,
        enableSelectionMode,
        isAllSelected,
        isSomeSelected,
        selectionReadStatus,
        selectionStarredStatus,
    } = useThreadSelection();

    const { getItemProps, onKeyDown: handleListboxKeyDown, onBlur: handleListboxBlur } = useThreadListbox(threads?.results);

    const isNative = isNativePlatform();
    // Mounted once for the whole list — see `useThreadRowActions`.
    const rowActions = useThreadRowActions();
    const { isRefreshing: isMailboxRefreshing, feedback: refreshFeedback, clearFeedback, refresh } = useRefreshFeedback();
    const { containerRef: pullToRefreshRef, indicatorRef } = usePullToRefresh({
        onRefresh: () => refresh(invalidateMailbox),
        enabled: isNative,
        threshold: PULL_TO_REFRESH_THRESHOLD,
    });

    // Single node feeding both the scroll-restore ref object and the
    // pull-to-refresh callback ref.
    const setThreadsListNode = useCallback((node: HTMLDivElement | null) => {
        scrollContainerRef.current = node;
        pullToRefreshRef(node);
    }, [scrollContainerRef, pullToRefreshRef]);

    const handleThreadsListScroll = useCallback(() => {
        // A row left open by a swipe would otherwise scroll away still open,
        // and come back holding actions the user has moved on from.
        closeSwipedRows();
        handleScroll();
    }, [handleScroll]);

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

    // Auto-close the thread view only when a thread that was previously
    // visible in the list disappears (bulk archive/trash). A thread reached
    // via a deep-link is allowed to stay open even when it is not part of
    // the current filtered list (e.g. archived thread opened from a shared
    // URL while viewing the inbox). The ref stores the id of the last
    // selected thread we've seen in the list — keyed by id so that
    // switching from an in-list thread to an out-of-list deep-link does
    // not trigger an erroneous auto-close on the new selection.
    const lastInListThreadIdRef = useRef<string | null>(null);
    useEffect(() => {
        if (!selectedThread) {
            lastInListThreadIdRef.current = null;
            return;
        }
        const isInList = threads?.results.some((thread) => thread.id === selectedThread.id) ?? false;
        if (isInList) {
            lastInListThreadIdRef.current = selectedThread.id;
        } else if (lastInListThreadIdRef.current === selectedThread.id) {
            unselectThread();
            lastInListThreadIdRef.current = null;
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
                onDeselectAll={deselectAllThreads}
                onClearSelection={clearSelection}
                onEnableSelectionMode={enableSelectionMode}
                onDisableSelectionMode={clearSelection}
                isRefreshing={isMailboxRefreshing}
                refreshFeedback={refreshFeedback}
                onClearRefreshFeedback={clearFeedback}
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
                <>
                    {isNative && <PullToRefreshIndicator ref={indicatorRef} />}
                    <div
                        className="thread-panel__threads_list"
                        ref={setThreadsListNode}
                        onScroll={handleThreadsListScroll}
                        role="listbox"
                        aria-multiselectable="true"
                        aria-label={t('Thread list')}
                        onKeyDown={handleListboxKeyDown}
                        onBlur={handleListboxBlur}
                    >
                    {threads?.results.map((thread) => (
                        // Swiping and selecting compete for the same finger:
                        // while a selection is running, taps stay dedicated to
                        // picking rows.
                        <ThreadItemSwipe
                            key={thread.id}
                            thread={thread}
                            enabled={isNative && !isSelectionMode && selectedThreadIds.size === 0}
                            actions={rowActions}
                        >
                            <ThreadItem
                                thread={thread}
                                isSelected={selectedThreadIds.has(thread.id)}
                                onToggle={toggleThread}
                                onSelectRange={selectRange}
                                selectedThreadIds={selectedThreadIds}
                                isSelectionMode={isSelectionMode}
                                {...getItemProps(thread.id)}
                            />
                        </ThreadItemSwipe>
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
                </>
            )}
        </div>
    );
}

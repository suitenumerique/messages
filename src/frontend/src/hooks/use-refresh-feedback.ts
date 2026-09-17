import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";

/** Minimum time the refreshing state stays on, so the feedback never just flashes. */
const MIN_REFRESH_MS = 700;

type UseRefreshFeedbackResult = {
    /** True while a refresh is in flight (kept on for at least MIN_REFRESH_MS). */
    isRefreshing: boolean;
    /** Result once a refresh settles: "Up to date" or "{{count}} new message". */
    feedback: string | null;
    clearFeedback: () => void;
    /**
     * Refresh the mailbox and compute the new-message feedback. `extra` runs
     * alongside the mailboxes refetch (e.g. invalidating the thread list on pull).
     */
    refresh: (extra?: () => Promise<unknown>) => Promise<void>;
};

/**
 * Refresh the selected mailbox and surface a transient "new messages" feedback.
 *
 * Shared by the left-panel refresh button and the thread-list pull-to-refresh so
 * both report the unread delta the exact same way.
 */
export const useRefreshFeedback = (): UseRefreshFeedbackResult => {
    const { t } = useTranslation();
    const { selectedMailbox, refetchMailboxes } = useMailboxContext();
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [feedback, setFeedback] = useState<string | null>(null);
    // Snapshot of count_unread_threads at refresh start. Read once isRefreshing
    // flips back to false AND React has committed the new mailbox data — at that
    // point we know the delta.
    const baselineUnreadCountRef = useRef<number | null>(null);
    // The re-entrance guard lives in a ref: `refresh` is handed to gesture
    // hooks that may call the instance of a previous render.
    const isRefreshingRef = useRef(false);
    const unreadCount = selectedMailbox?.count_unread_threads ?? 0;

    useEffect(() => {
        if (isRefreshing || baselineUnreadCountRef.current === null) return;
        const delta = unreadCount - baselineUnreadCountRef.current;
        baselineUnreadCountRef.current = null;
        setFeedback(
            delta > 0
                ? t("{{count}} new message", { count: delta })
                : t("Up to date"),
        );
    }, [isRefreshing, unreadCount, t]);

    const clearFeedback = useCallback(() => setFeedback(null), []);

    const refresh = useCallback(async (extra?: () => Promise<unknown>) => {
        if (isRefreshingRef.current) return;
        isRefreshingRef.current = true;
        baselineUnreadCountRef.current = unreadCount;
        setFeedback(null);
        setIsRefreshing(true);
        try {
            const [result] = await Promise.all([
                refetchMailboxes(),
                extra?.(),
                new Promise<void>((resolve) => window.setTimeout(resolve, MIN_REFRESH_MS)),
            ]);
            // A refetch failure resolves rather than throws. The global query
            // error handler already reports it: the only thing to do here is
            // to withhold the verdict, "Up to date" would be a lie.
            if (result?.isError) baselineUnreadCountRef.current = null;
        } finally {
            isRefreshingRef.current = false;
            setIsRefreshing(false);
        }
    }, [refetchMailboxes, unreadCount]);

    return { isRefreshing, feedback, clearFeedback, refresh };
};

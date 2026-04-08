import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getMailboxThreadsListQueryKeyPrefix, useMailboxContext } from "@/features/providers/mailbox";
import { threadsEventsReadMentionPartialUpdate } from "@/features/api/gen/thread-events/thread-events";

type UseMentionReadReturn = {
    markMentionsRead: (threadEventIds: string[]) => void;
};

/**
 * Hook to acknowledge mention UserEvents as read for a given thread.
 *
 * The thread id is bound at init time because the backend endpoint is
 * nested under `/threads/{thread_id}/events/`, and because a given caller
 * (typically the thread view) is always scoped to a single thread. The
 * endpoint is unitary (PATCH per ThreadEvent); this hook fans out to N
 * parallel calls when the intersection observer batches several events in
 * the same debounce window.
 *
 * Cache strategy (invalidation-only, no optimistic updates):
 *   1. Stats cache → invalidated on settle so the sidebar badge reflects
 *      the server-authoritative count.
 *   2. Thread list cache → invalidated on settle so threads leave the
 *      has_unread_mention=1 filter once no unread mention remains.
 *
 * We deliberately avoid optimistic updates on stats. The mailbox stats
 * cache is multi-keyed (global `['threads', 'stats', mailboxId]` coexists
 * with per-label `['threads', 'stats', mailboxId, 'label_slug=…']` entries
 * under the same prefix), so any `setQueriesData` here would fan out to
 * label counters that must not be touched. Keeping this flow
 * invalidation-only is simpler and stays consistent with how the rest of
 * the app treats the stats cache (see `invalidateThreadsStats`).
 *
 * The thread events cache is also deliberately NOT touched. Keeping
 * `has_unread_mention=true` on the currently displayed thread events means
 * the "Mentioned" badge stays visible for the whole thread session, giving
 * the user time to actually notice why the thread was flagged. The cache
 * gets refreshed naturally on the next refetch (thread switch + return,
 * window refocus, manual refresh), at which point the badge disappears.
 */
const useMentionRead = (threadId: string): UseMentionReadReturn => {
    const { selectedMailbox, invalidateThreadsStats } = useMailboxContext();
    const queryClient = useQueryClient();

    const markMentionsRead = useCallback((threadEventIds: string[]) => {
        if (!threadEventIds.length) return;

        Promise.all(
            threadEventIds.map((id) =>
                threadsEventsReadMentionPartialUpdate(threadId, id),
            ),
        )
            .catch(() => {
                // Swallow: the invalidation below will reconcile with the
                // server state, so a transient PATCH failure is self-healing
                // on the next refetch.
            })
            .finally(() => {
                invalidateThreadsStats();
                queryClient.invalidateQueries({ queryKey: getMailboxThreadsListQueryKeyPrefix(selectedMailbox?.id) });
            });
    }, [threadId, selectedMailbox?.id, queryClient, invalidateThreadsStats]);

    return { markMentionsRead };
};

export default useMentionRead;

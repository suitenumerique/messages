import { QueryClient } from "@tanstack/react-query";
import { LabelsStatsRetrieveStatsFields, useLabelsStatsRetrieve } from "@/features/api/gen";
import { combineStatsFields } from "./use-threads-stats";

/**
 * Prefix of the label counts queries of every mailbox. Kept apart from the
 * threads stats subtree, whose invalidations would otherwise reach it.
 */
const LABELS_STATS_QUERY_KEY = ['labels', 'stats'] as const;

const getLabelsStatsQueryKey = (mailboxId: string | undefined, statsFields: string) =>
    [...LABELS_STATS_QUERY_KEY, mailboxId, statsFields];

/**
 * Minimum delay between two fetches of the label counts. They are summed
 * over every labelled thread of the mailbox, so refetching them on each
 * change would weigh on a busy shared mailbox.
 */
export const LABELS_STATS_STALE_TIME = 5 * 60 * 1000;

/**
 * Flag the label counts as outdated, after a change that can move them:
 * read state, spam or trash, labels or access of a thread. `useLabelStats`
 * then refetches them at most once per `LABELS_STATS_STALE_TIME`.
 *
 * Every mailbox is flagged: callers run once their request settles, when
 * the selected mailbox may no longer be the one it changed. The others are
 * not fetched, only refreshed when shown again.
 */
export const invalidateLabelsStats = (queryClient: QueryClient) =>
    queryClient.invalidateQueries({
        queryKey: LABELS_STATS_QUERY_KEY,
        refetchType: 'none',
    });

type UseLabelStatsOptions = {
    mailboxId: string | undefined;
    labelId: string;
    fields: readonly LabelsStatsRetrieveStatsFields[];
};

/**
 * Counts of a label of the mailbox. Every label shares a single
 * `/labels/stats/` request, refreshed after `invalidateLabelsStats` at most
 * once per `LABELS_STATS_STALE_TIME`.
 */
export const useLabelStats = ({ mailboxId, labelId, fields }: UseLabelStatsOptions) => {
    const statsFields = combineStatsFields(fields);
    const { data } = useLabelsStatsRetrieve({
        mailbox_id: mailboxId ?? '',
        stats_fields: statsFields,
    }, {
        query: {
            enabled: !!mailboxId,
            queryKey: getLabelsStatsQueryKey(mailboxId, statsFields),
            // Unfolding a label mounts its children: reuse the counts.
            staleTime: LABELS_STATS_STALE_TIME,
            // Once invalidated, refetch when `LABELS_STATS_STALE_TIME` has
            // elapsed since the last attempt: a burst of changes costs one
            // request, and the last of them still reaches the badges. Failed
            // attempts are retried at the same pace. A hidden tab skips the
            // ticks, so its first tick once visible again does the refetch;
            // the one-second floor keeps those skipped ticks cheap.
            refetchInterval: ({ state }) => {
                if (!state.isInvalidated || state.fetchStatus === 'fetching') return false;
                const lastAttemptAt = Math.max(state.dataUpdatedAt, state.errorUpdatedAt);
                return Math.max(lastAttemptAt + LABELS_STATS_STALE_TIME - Date.now(), 1000);
            },
            select: (response) => response.status === 200 ? response.data[labelId] : undefined,
        },
    });
    return data;
};

import { ThreadsStatsRetrieveStatsFields, useThreadsStatsRetrieve } from "@/features/api/gen";

/**
 * Canonical query key for the threads stats query.
 *
 * Single source of truth shared by:
 *   - `useThreadsStats`, which passes `queryParams` to scope the cache per
 *     filter/label and requested fields
 *   - invalidation / optimistic-update sites that target the whole
 *     per-mailbox stats subtree (omit `queryParams` for prefix matching)
 *
 * Keep in sync with the `invalidateThreadsStats` predicate, which relies
 * on `queryParams` being the last entry to filter out `label_slug=*` keys.
 */
export const getThreadsStatsQueryKey = (
    mailboxId: string | undefined,
    queryParams?: string,
) => {
    const base = ['threads', 'stats', mailboxId];
    return queryParams !== undefined ? [...base, queryParams] : base;
};

/**
 * What a folder counts: `count` is the field its sidebar badge and
 * visibility rules read, `extra` the fields other consumers need. All of
 * them are fetched in a single request shared by every consumer.
 */
export type FolderStatsDefinition = {
    count: ThreadsStatsRetrieveStatsFields;
    extra?: readonly ThreadsStatsRetrieveStatsFields[];
};

type StatsFolder = {
    filter?: Readonly<Record<string, string>>;
    stats?: FolderStatsDefinition;
};

type UseThreadsStatsOptions = {
    mailboxId: string | undefined;
    filter?: Readonly<Record<string, string>>;
    fields: readonly ThreadsStatsRetrieveStatsFields[];
    enabled?: boolean;
};

const DEFAULT_FOLDER_STATS: FolderStatsDefinition = { count: ThreadsStatsRetrieveStatsFields.all_unread };

/**
 * The API takes a comma-separated list of fields, which the generated type
 * does not model. Sorted so that the same set always yields the same key.
 */
const combineStatsFields = (fields: readonly ThreadsStatsRetrieveStatsFields[]) =>
    [...new Set(fields)].sort().join(',') as ThreadsStatsRetrieveStatsFields;

const getStatsQueryParams = (
    filter: Readonly<Record<string, string>>,
    statsFields: ThreadsStatsRetrieveStatsFields,
) => new URLSearchParams({ ...filter, stats_fields: statsFields }).toString();

const getFolderStatsFields = ({ stats = DEFAULT_FOLDER_STATS }: StatsFolder) =>
    [stats.count, ...(stats.extra ?? [])];

/**
 * Query key of a folder's stats, for optimistic updates of its counts.
 */
export const getFolderStatsQueryKey = (mailboxId: string | undefined, folder: StatsFolder) =>
    getThreadsStatsQueryKey(
        mailboxId,
        getStatsQueryParams(folder.filter ?? {}, combineStatsFields(getFolderStatsFields(folder))),
    );

/**
 * Counts of the threads of a mailbox matching `filter`. The requested fields
 * belong to the cache key, so two consumers asking different counts of the
 * same filter never overwrite each other.
 */
export const useThreadsStats = ({ mailboxId, filter = {}, fields, enabled = true }: UseThreadsStatsOptions) => {
    const statsFields = combineStatsFields(fields);
    const { data } = useThreadsStatsRetrieve({
        mailbox_id: mailboxId,
        stats_fields: statsFields,
        ...filter,
    }, {
        query: {
            enabled: enabled && !!mailboxId,
            queryKey: getThreadsStatsQueryKey(mailboxId, getStatsQueryParams(filter, statsFields)),
        },
    });
    return data?.status === 200 ? data.data : undefined;
};

/**
 * Stats of a sidebar folder, as its definition declares them. Any consumer
 * of a folder's counts goes through here to share the sidebar request.
 */
export const useFolderStats = (
    mailboxId: string | undefined,
    folder: StatsFolder,
    { enabled = true }: { enabled?: boolean } = {},
) => {
    const stats = useThreadsStats({
        mailboxId,
        filter: folder.filter,
        fields: getFolderStatsFields(folder),
        enabled,
    });
    return { stats, count: stats?.[(folder.stats ?? DEFAULT_FOLDER_STATS).count] };
};

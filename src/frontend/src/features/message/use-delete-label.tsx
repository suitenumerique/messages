import { useLabelsRemoveThreadsCreate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";

type DeleteLabelOptions = {
    labelId: string;
    labelSlug: string;
    threadIds: string[];
    onSuccess?: () => void;
}

/**
 * Hook to remove a label from one or more threads.
 *
 * Two regimes depending on whether the active view filters by the affected
 * slug:
 *   - filter targets the slug: drop any pin BEFORE invalidating so
 *     `mergePinnedThreads` does not re-insert the now-excluded thread (a
 *     thread previously marked-as-read or starred in this view is pinned).
 *   - filter does not target the slug: patch the cached labels list in place.
 *     Without this patch, a pinned thread filtered out on the next refetch
 *     would be re-inserted from cache with its stale label still attached.
 */
const useDeleteLabel = () => {
    const { invalidateMailbox, unpinThreads, pinThreads } = useMailboxContext();
    const searchParams = useUrlSearchParams();
    const { mutate, status } = useLabelsRemoveThreadsCreate();

    const deleteLabel = ({ labelId, labelSlug, threadIds, onSuccess }: DeleteLabelOptions) => {
        mutate({
            id: labelId,
            data: { thread_ids: threadIds },
        }, {
            onSuccess: () => {
                if (searchParams.get('label_slug') === labelSlug) {
                    unpinThreads(threadIds);
                } else {
                    pinThreads(threadIds, (thread) => ({
                        ...thread,
                        labels: thread.labels.filter((l) => l.id !== labelId),
                    }));
                }
                invalidateMailbox();
                onSuccess?.();
            }
        });
    };

    return { deleteLabel, status };
};

export default useDeleteLabel;

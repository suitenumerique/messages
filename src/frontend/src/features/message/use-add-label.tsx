import { ThreadLabel, useLabelsAddThreadsCreate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";

type AddLabelOptions = {
    label: ThreadLabel;
    threadIds: string[];
    onSuccess?: () => void;
}

/**
 * Hook to add a label to one or more threads.
 *
 * Patch the cached threads BEFORE invalidating: a pinned thread (e.g. one
 * marked-as-read while viewing "unread") is filtered out by the server on the
 * next refetch, and `mergePinnedThreads` would re-insert the cached version.
 * Without the local patch, that cached version still carries the old label
 * list — the new label never shows up visually.
 */
const useAddLabel = () => {
    const { invalidateMailbox, pinThreads } = useMailboxContext();
    const { mutate, status } = useLabelsAddThreadsCreate();

    const addLabel = ({ label, threadIds, onSuccess }: AddLabelOptions) => {
        mutate({
            id: label.id,
            data: { thread_ids: threadIds },
        }, {
            onSuccess: () => {
                pinThreads(threadIds, (thread) => {
                    if (thread.labels.some((l) => l.id === label.id)) return thread;
                    return { ...thread, labels: [...thread.labels, label] };
                });
                invalidateMailbox();
                onSuccess?.();
            }
        });
    };

    return { addLabel, status };
};

export default useAddLabel;

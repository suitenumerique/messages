import { useThreadsAccessesDestroy } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";

type DeleteThreadAccessOptions = {
    accessId: string;
    accessMailboxId: string;
    threadId: string;
    onSuccess?: () => void;
}

/**
 * Hook to remove a ThreadAccess.
 *
 * On self-removal (the access belongs to the currently selected mailbox), the
 * thread vanishes from the user's view: drop any pin on it BEFORE invalidating
 * so `mergePinnedThreads` does not re-insert it on the next refetch (e.g. when
 * the thread had been pinned earlier in this view by mark-as-read or star).
 */
const useDeleteThreadAccess = () => {
    const { selectedMailbox, invalidateMailbox, invalidateThreadsStats, unpinThreads, unselectThread } = useMailboxContext();
    const { mutate, status } = useThreadsAccessesDestroy();

    const deleteThreadAccess = ({ accessId, accessMailboxId, threadId, onSuccess }: DeleteThreadAccessOptions) => {
        const isSelfRemoval = accessMailboxId === selectedMailbox?.id;
        mutate({ id: accessId, threadId }, {
            onSuccess: () => {
                if (isSelfRemoval) {
                    unpinThreads([threadId]);
                }
                invalidateMailbox();
                if (isSelfRemoval) {
                    invalidateThreadsStats();
                    unselectThread();
                }
                onSuccess?.();
            }
        });
    };

    return { deleteThreadAccess, status };
};

export default useDeleteThreadAccess;

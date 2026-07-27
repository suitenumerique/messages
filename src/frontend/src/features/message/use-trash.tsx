import { useQueryClient } from "@tanstack/react-query";
import { getMailboxesStorageRetrieveQueryKey } from "@/features/api/gen";
import { useMailboxContext } from "../providers/mailbox";
import { useTranslation } from "react-i18next";
import useFlag from "./use-flag";

/**
 * Hook to mark messages or threads as trashed
 */
const useTrash = () => {
    const { t } = useTranslation();
    const { selectedMailbox, invalidateMailbox, invalidateThreadsStats, unpinThreads } = useMailboxContext();
    const queryClient = useQueryClient();

    const { mark, unmark, status } = useFlag('trashed', {
        toastMessages: {
            thread: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No thread could be moved to the trash.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} threads have been moved to the trash.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} thread has been moved to the trash.' });
                return t('{{count}} threads have been moved to the trash.', { count: updatedCount, defaultValue_one: 'The thread has been moved to the trash.' });
            },
            message: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No message could be moved to the trash.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} messages have been moved to the trash.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} message has been moved to the trash.' });
                return t('{{count}} messages have been moved to the trash.', { count: updatedCount, defaultValue_one: 'The message has been moved to the trash.' });
            },
        },
        // Also runs when the toast's "Undo" restores the threads.
        onSuccess: (data) => {
            unpinThreads(data.thread_ids ?? []);
            invalidateMailbox();
            invalidateThreadsStats();
            // Trashing moves storage to the trash subtotal and out of the
            // largest threads list of the storage settings.
            const mailboxId = data.mailbox_id ?? selectedMailbox?.id;
            if (mailboxId) {
                queryClient.invalidateQueries({ queryKey: getMailboxesStorageRetrieveQueryKey(mailboxId) });
            }
        }
    });

    return {
        markAsTrashed: mark,
        markAsUntrashed: unmark,
        status
    };
};

export default useTrash;

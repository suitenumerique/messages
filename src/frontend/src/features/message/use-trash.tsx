import { useMailboxContext } from "../providers/mailbox";
import { useTranslation } from "react-i18next";
import useFlag from "./use-flag";

/**
 * Hook to mark messages or threads as trashed
 */
const useTrash = () => {
    const { t } = useTranslation();
    const { invalidateMailbox, invalidateThreadsStats, unpinThreads } = useMailboxContext();

    const { mark, unmark, status } = useFlag('trashed', {
        toastMessages: {
            thread: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No thread could be deleted.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} threads have been deleted.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} thread has been deleted.' });
                return t('{{count}} threads have been deleted.', { count: updatedCount, defaultValue_one: 'The thread has been deleted.' });
            },
            message: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No message could be deleted.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} messages have been deleted.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} message has been deleted.' });
                return t('{{count}} messages have been deleted.', { count: updatedCount, defaultValue_one: 'The message has been deleted.' });
            },
        },
        onSuccess: (data) => {
            unpinThreads(data.thread_ids ?? []);
            invalidateMailbox();
            invalidateThreadsStats();
        }
    });

    return {
        markAsTrashed: mark,
        markAsUntrashed: unmark,
        status
    };
};

export default useTrash;

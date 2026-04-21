import { useMailboxContext } from "../providers/mailbox";
import { useTranslation } from "react-i18next";
import useFlag from "./use-flag";

type UseArchiveOptions = {
    showToast?: boolean;
}

/**
 * Hook to mark messages or threads as archived
 */
const useArchive = (options?: UseArchiveOptions) => {
    const { t } = useTranslation();
    const { invalidateMailbox, invalidateThreadsStats, unpinThreads } = useMailboxContext();

    const { mark, unmark, status } = useFlag('archived', {
        showToast: options?.showToast,
        toastMessages: {
            thread: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No thread could be archived.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} threads have been archived.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} thread has been archived.' });
                return t('{{count}} threads have been archived.', { count: updatedCount, defaultValue_one: 'The thread has been archived.' });
            },
            message: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No message could be archived.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} messages have been archived.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} message has been archived.' });
                return t('{{count}} messages have been archived.', { count: updatedCount, defaultValue_one: 'The message has been archived.' });
            },
        },
        onSuccess: (data) => {
            unpinThreads(data.thread_ids ?? []);
            invalidateMailbox();
            invalidateThreadsStats();
        }
    });

    return {
        markAsArchived: mark,
        markAsUnarchived: unmark,
        status
    }
};

export default useArchive;

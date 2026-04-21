import { useTranslation } from "react-i18next";
import useFlag from "./use-flag";
import { useMailboxContext } from "../providers/mailbox";

/**
 * Hook to mark messages or threads as spam
 */
const useSpam = () => {
    const { t } = useTranslation();
    const { invalidateMailbox, invalidateThreadsStats, unpinThreads } = useMailboxContext();
    const { mark, unmark, status } = useFlag('spam', {
        toastMessages: {
            thread: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No thread could be reported as spam.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} threads have been reported as spam.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} thread has been reported as spam.' });
                return t('{{count}} threads have been reported as spam.', { count: updatedCount, defaultValue_one: 'The thread has been reported as spam.' });
            },
            message: (updatedCount, submittedCount) => {
                if (updatedCount === 0) return t('No message could be reported as spam.');
                if (updatedCount < submittedCount) return t('{{count}} out of {{total}} messages have been reported as spam.', { count: updatedCount, total: submittedCount, defaultValue_one: '{{count}} out of {{total}} message has been reported as spam.' });
                return t('{{count}} messages have been reported as spam.', { count: updatedCount, defaultValue_one: 'The message has been reported as spam.' });
            },
        },
        onSuccess: (data) => {
            unpinThreads(data.thread_ids ?? []);
            invalidateMailbox();
            invalidateThreadsStats();
        },
    });

    return {
        markAsSpam: mark,
        markAsNotSpam: unmark,
        status
    };
};

export default useSpam;

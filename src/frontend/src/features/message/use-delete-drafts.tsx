import { useMailboxContext } from "../providers/mailbox";
import { useTranslation } from "react-i18next";
import useDelete from "./use-delete";

/**
 * Hook to permanently delete draft messages. Drafts have no trash stage, so
 * deletion is immediate and irreversible (no confirmation, no undo).
 */
const useDeleteDrafts = () => {
    const { t } = useTranslation();
    const { invalidateMailbox, invalidateThreadsStats } = useMailboxContext();

    const { remove, status } = useDelete("draft", {
        toastMessage: (deletedCount, submittedCount) => {
            if (deletedCount === 0) return t("No draft could be deleted.");
            if (deletedCount < submittedCount) {
                return t("{{count}} out of {{total}} drafts have been deleted.", {
                    count: deletedCount,
                    total: submittedCount,
                    defaultValue_one: "{{count}} out of {{total}} draft has been deleted.",
                });
            }
            return t("{{count}} drafts have been deleted.", {
                count: deletedCount,
                defaultValue_one: "The draft has been deleted.",
            });
        },
        onSuccess: () => {
            invalidateMailbox();
            invalidateThreadsStats();
        },
    });

    return {
        deleteDrafts: remove,
        status,
    };
};

export default useDeleteDrafts;

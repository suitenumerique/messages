import { useModals } from "@gouvfr-lasuite/ui-components";
import { useMailboxContext } from "../providers/mailbox";
import { useComposeWindows } from "../providers/compose-windows";
import { useTranslation } from "react-i18next";
import useDelete, { DeleteOptions } from "./use-delete";

/**
 * Hook to permanently delete draft messages. Drafts have no trash stage, so
 * deletion is irreversible: it is always gated behind a confirmation modal.
 */
const useDeleteDrafts = () => {
    const { t } = useTranslation();
    const modals = useModals();
    const { selectedMailbox, invalidateMailbox, invalidateThreadsStats } = useMailboxContext();
    const { closeDeletedDraftWindows } = useComposeWindows();

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
        onSuccess: (_deletedCount, { messageIds, threadIds }) => {
            invalidateMailbox();
            invalidateThreadsStats();
            // A compose window left open on a deleted draft would keep
            // autosaving it into 404s. Wired here rather than on the per-call
            // callback, which React Query skips once the caller unmounted
            // (e.g. leaving the thread on mobile before the response).
            if (messageIds.length > 0) {
                closeDeletedDraftWindows({ draftIds: messageIds, threadIds, mailboxId: selectedMailbox?.id });
            }
        },
    });

    const deleteDrafts = async (options: DeleteOptions) => {
        const count = options.threadIds?.length || options.messageIds?.length || 0;
        const decision = await modals.deleteConfirmationModal({
            title: count > 1 ? t("Delete drafts") : t("Delete draft"),
            children: t("Are you sure you want to delete these {{count}} drafts? This action cannot be undone.", {
                count,
                defaultValue_one: "Are you sure you want to delete this draft? This action cannot be undone.",
            }),
        });
        if (decision !== "delete") return;
        remove(options);
    };

    return {
        deleteDrafts,
        status,
    };
};

export default useDeleteDrafts;

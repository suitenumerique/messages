import { useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { useMailboxesEmptyTrashCreate } from "@/features/api/gen";
import { MailboxEmptyTrashRequestScopeEnum } from "@/features/api/gen/models";
import { errorToString } from "@/features/api/api-error";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useMailboxContext } from "../providers/mailbox";

type EmptyTrashOptions = {
    mailboxId: string;
    scope: MailboxEmptyTrashRequestScopeEnum;
    /** Restrict the deletion to these conversations. Omit to empty the folder. */
    threadIds?: string[];
    /** Restrict the deletion to these messages. Omit to empty the folder. */
    messageIds?: string[];
    onSuccess?: (deletedCount: number) => void;
};

/**
 * Permanently delete from one folder of the trashbin (trashed OR spam). Unlike
 * `useTrash` (a reversible soft-delete flag), this hard-deletes and cannot be
 * undone — so it always goes through a scary confirmation dialog first and its
 * toast never offers "Undo".
 *
 * Deletes the whole folder by default; pass `threadIds`/`messageIds` to remove
 * only a selection. Both go through the same backend action and the same
 * `empty_trash` permission — deleting one trashed message is exactly as
 * irreversible as emptying the folder, so it is gated identically.
 */
const useEmptyTrash = () => {
    const { t } = useTranslation();
    const modals = useModals();
    const { invalidateMailbox, invalidateThreadsStats } = useMailboxContext();
    const { mutate, status } = useMailboxesEmptyTrashCreate();

    const emptyTrashbin = async ({
        mailboxId,
        scope,
        threadIds,
        messageIds,
        onSuccess,
    }: EmptyTrashOptions) => {
        const isSpam = scope === "spam";
        // A selection was passed, so this is a targeted delete rather than a
        // whole-folder wipe. Kept count-free on purpose: a plural key here would
        // need _one/_other variants, and the surrounding file uses flat keys.
        const isTargeted = Boolean(threadIds?.length || messageIds?.length);
        const title = isTargeted
            ? t("Delete permanently")
            : isSpam
              ? t("Empty spam")
              : t("Empty trash");
        const warning = isTargeted
            ? t("You are about to permanently delete the selected messages. This cannot be undone.")
            : isSpam
              ? t("You are about to permanently delete every message in the spam folder. This cannot be undone.")
              : t("You are about to permanently delete every message in the trash. This cannot be undone.");

        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{title}</span>,
            children: (
                <Banner type="warning">
                    {warning}
                </Banner>
            ),
        });

        if (decision !== "delete") return;

        mutate(
            {
                id: mailboxId,
                data: {
                    scope,
                    ...(threadIds?.length ? { thread_ids: threadIds } : {}),
                    ...(messageIds?.length ? { message_ids: messageIds } : {}),
                },
            },
            {
                onSuccess: (response) => {
                    const data = response.data as { deleted_count?: number };
                    const deletedCount = data?.deleted_count ?? 0;
                    invalidateMailbox();
                    invalidateThreadsStats();
                    addToast(
                        <ToasterItem type="info">
                            <p>
                                {isSpam
                                    ? t("{{count}} spam messages have been permanently deleted.", {
                                          count: deletedCount,
                                          defaultValue_one: "{{count}} spam message has been permanently deleted.",
                                      })
                                    : t("{{count}} messages have been permanently deleted.", {
                                          count: deletedCount,
                                          defaultValue_one: "{{count}} message has been permanently deleted.",
                                      })}
                            </p>
                        </ToasterItem>
                    );
                    onSuccess?.(deletedCount);
                },
                onError: (error) => {
                    addToast(
                        <ToasterItem type="error">
                            <p>{errorToString(error)}</p>
                        </ToasterItem>
                    );
                },
            }
        );
    };

    return { emptyTrashbin, status };
};

export default useEmptyTrash;

import { useTranslation } from "react-i18next";
import { Restore, Trash } from "@gouvfr-lasuite/ui-components/icons";
import { MailboxEmptyTrashRequestScopeEnum, Thread } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import { IconProps } from "@/features/ui/components/icon";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useCurrentView } from "@/hooks/use-current-view";
import useDeleteDrafts from "./use-delete-drafts";
import useEmptyTrash from "./use-empty-trash";
import useTrash from "./use-trash";

export type ThreadRemoveAction = {
    key: "delete-draft" | "restore" | "delete-permanently" | "move-to-trash";
    label: string;
    icon: IconProps;
    onSelect: () => void;
    isPending?: boolean;
};

/**
 * The actions taking the open thread out of the current folder, shared by the
 * desktop action bar and the mobile toolbar so both follow the same rules:
 * - drafts view: delete the draft;
 * - trash context (trash view, or a fully trashed thread opened elsewhere):
 *   restore, and permanently delete the messages already in the trash;
 * - spam thread: permanently delete its spam, as it already sits in the trashbin;
 * - anywhere else: move to trash.
 *
 * A partially trashed thread opened from the trash view is in the trash
 * context: trashing it would also bin its live messages.
 */
const useThreadRemoveActions = (
    thread: Thread | null | undefined,
    isTrashContext: boolean,
): ThreadRemoveAction[] => {
    const { t } = useTranslation();
    const { selectedMailbox, unselectThread } = useMailboxContext();
    const { isDraftsView } = useCurrentView();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { deleteDrafts } = useDeleteDrafts();
    const { emptyTrashbin, isPending: isDeletingPermanently } = useEmptyTrash();
    const canEditThread = useAbility(Abilities.CAN_EDIT_THREAD, thread ?? null);
    const canEmptyTrash = useAbility(Abilities.CAN_EMPTY_TRASH, selectedMailbox);

    if (!thread || !canEditThread) return [];
    const threadIds = [thread.id];

    if (isDraftsView) {
        return [{
            key: "delete-draft",
            label: t("Delete draft"),
            icon: { icon: Trash },
            onSelect: () => deleteDrafts({ threadIds, onSuccess: unselectThread }),
        }];
    }

    const trashbinScope: MailboxEmptyTrashRequestScopeEnum | null = isTrashContext
        ? "trashed"
        : thread.is_spam ? "spam" : null;

    if (!trashbinScope) {
        return [{
            key: "move-to-trash",
            label: t("Move to trash"),
            icon: { icon: Trash },
            onSelect: () => markAsTrashed({ threadIds, onSuccess: unselectThread }),
        }];
    }

    const actions: ThreadRemoveAction[] = [];
    if (isTrashContext) {
        actions.push({
            key: "restore",
            label: t("Restore"),
            icon: { icon: Restore },
            onSelect: () => markAsUntrashed({ threadIds, onSuccess: unselectThread }),
        });
    }
    if (canEmptyTrash && selectedMailbox) {
        actions.push({
            key: "delete-permanently",
            label: t("Delete permanently"),
            icon: { icon: Trash },
            isPending: isDeletingPermanently,
            onSelect: () => emptyTrashbin({
                mailboxId: selectedMailbox.id,
                scope: trashbinScope,
                threadIds,
                onSuccess: unselectThread,
            }),
        });
    }
    return actions;
};

export default useThreadRemoveActions;

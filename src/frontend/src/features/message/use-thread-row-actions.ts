import { useCallback, useMemo, useRef } from "react";

import { Thread } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import useAbility, { Abilities } from "@/hooks/use-ability";
import useArchive from "./use-archive";
import useDeleteDrafts from "./use-delete-drafts";
import useEmptyTrash from "./use-empty-trash";
import useRead from "./use-read";
import useTrash from "./use-trash";

export type ThreadRowActions = {
    /** `isUnread` is the state to leave behind, not the one to apply. */
    toggleRead: (thread: Thread, isUnread: boolean) => void;
    setArchived: (thread: Thread, archived: boolean) => void;
    setTrashed: (thread: Thread, trashed: boolean) => void;
    /** Drafts have no trash stage: this permanently deletes the thread's drafts, after confirmation. */
    deleteDrafts: (thread: Thread) => void;
    /** Spam is already in the trashbin: it is permanently deleted, after confirmation, instead of trashed. */
    deleteSpam: (thread: Thread) => void;
    /** Read once here rather than per row: it depends on the mailbox, not the thread. */
    canDeleteSpam: boolean;
};

/**
 * Row-level actions for a whole thread list, mounted **once** by the list
 * rather than by each row.
 *
 * Every one of these hooks instantiates React Query mutations and subscribes to
 * the mailbox context; per row that is four mutations and four context
 * subscriptions, so a fifty-row list paid for two hundred of each and
 * re-rendered all of them whenever the context changed. That cost showed up as
 * dropped frames during the swipe on low-end devices.
 *
 * The returned object is referentially stable (it only changes with the
 * mailbox's `canDeleteSpam` ability), so passing it down a list does not
 * invalidate memoised rows.
 */
export const useThreadRowActions = (): ThreadRowActions => {
    const { markAsReadAt } = useRead();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { deleteDrafts: removeDrafts } = useDeleteDrafts();
    const { emptyTrashbin } = useEmptyTrash();
    const { selectedMailbox } = useMailboxContext();
    const canDeleteSpam = useAbility(Abilities.CAN_EMPTY_TRASH, selectedMailbox);

    // The mutation callbacks are rebuilt on every render of the host, so they
    // are read through a ref: the exposed actions must never change identity.
    const mutations = useRef({
        markAsReadAt,
        markAsArchived,
        markAsUnarchived,
        markAsTrashed,
        markAsUntrashed,
        removeDrafts,
        emptyTrashbin,
        mailboxId: selectedMailbox?.id,
    });
    mutations.current = {
        markAsReadAt,
        markAsArchived,
        markAsUnarchived,
        markAsTrashed,
        markAsUntrashed,
        removeDrafts,
        emptyTrashbin,
        mailboxId: selectedMailbox?.id,
    };

    const toggleRead = useCallback((thread: Thread, isUnread: boolean) => {
        mutations.current.markAsReadAt({
            threadIds: [thread.id],
            readAt: isUnread ? new Date().toISOString() : null,
        });
    }, []);

    const setArchived = useCallback((thread: Thread, archived: boolean) => {
        const mutate = archived
            ? mutations.current.markAsArchived
            : mutations.current.markAsUnarchived;
        mutate({ threadIds: [thread.id] });
    }, []);

    const setTrashed = useCallback((thread: Thread, trashed: boolean) => {
        const mutate = trashed
            ? mutations.current.markAsTrashed
            : mutations.current.markAsUntrashed;
        mutate({ threadIds: [thread.id] });
    }, []);

    const deleteDrafts = useCallback((thread: Thread) => {
        mutations.current.removeDrafts({ threadIds: [thread.id] });
    }, []);

    const deleteSpam = useCallback((thread: Thread) => {
        const { mailboxId } = mutations.current;
        if (!mailboxId) return;
        mutations.current.emptyTrashbin({ mailboxId, scope: "spam", threadIds: [thread.id] });
    }, []);

    return useMemo(
        () => ({ toggleRead, setArchived, setTrashed, deleteDrafts, deleteSpam, canDeleteSpam }),
        [toggleRead, setArchived, setTrashed, deleteDrafts, deleteSpam, canDeleteSpam]
    );
};

export default useThreadRowActions;

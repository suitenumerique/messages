import { useCallback, useMemo, useRef } from "react";

import { Thread } from "@/features/api/gen/models";
import useArchive from "./use-archive";
import useDeleteDrafts from "./use-delete-drafts";
import useRead from "./use-read";
import useTrash from "./use-trash";

export type ThreadRowActions = {
    /** `isUnread` is the state to leave behind, not the one to apply. */
    toggleRead: (thread: Thread, isUnread: boolean) => void;
    setArchived: (thread: Thread, archived: boolean) => void;
    setTrashed: (thread: Thread, trashed: boolean) => void;
    /** Drafts have no trash stage: this permanently deletes the thread's drafts. */
    deleteDrafts: (thread: Thread) => void;
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
 * The returned object is referentially stable, so passing it down a list does
 * not invalidate memoised rows.
 */
export const useThreadRowActions = (): ThreadRowActions => {
    const { markAsReadAt } = useRead();
    const { markAsArchived, markAsUnarchived } = useArchive();
    const { markAsTrashed, markAsUntrashed } = useTrash();
    const { deleteDrafts: removeDrafts } = useDeleteDrafts();

    // The mutation callbacks are rebuilt on every render of the host, so they
    // are read through a ref: the exposed actions must never change identity.
    const mutations = useRef({
        markAsReadAt,
        markAsArchived,
        markAsUnarchived,
        markAsTrashed,
        markAsUntrashed,
        removeDrafts,
    });
    mutations.current = {
        markAsReadAt,
        markAsArchived,
        markAsUnarchived,
        markAsTrashed,
        markAsUntrashed,
        removeDrafts,
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

    return useMemo(
        () => ({ toggleRead, setArchived, setTrashed, deleteDrafts }),
        [toggleRead, setArchived, setTrashed, deleteDrafts]
    );
};

export default useThreadRowActions;

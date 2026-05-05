import { useMemo, useState } from "react";
import {
    ThreadEventTypeEnum,
    useThreadsEventsCreate,
} from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useAssignedUsers } from "./use-assigned-users";

export type AssignableUser = {
    id: string;
    full_name?: string | null;
    email?: string | null;
};

export type UseThreadAssignmentResult = {
    assignedUserIds: ReadonlySet<string>;
    /**
     * IDs of users with an assign/unassign mutation in flight. A Set
     * (rather than a single scalar) is required because the UI can fire
     * concurrent toggles — tracking only the latest id would clear the
     * spinner / disabled state for an earlier still-pending request when
     * the next one settles.
     */
    mutatingUserIds: ReadonlySet<string>;
    /**
     * Mark a user as having a mutation in flight. Pair with
     * `clearUserMutating(id)` once the mutation settles.
     */
    markUserMutating: (id: string) => void;
    clearUserMutating: (id: string) => void;
    /**
     * High-level helper: marks the user as pending and fires the assign
     * event. Use for simple flows where the caller has nothing to do
     * before/after the mutation.
     */
    assignUser: (user: AssignableUser) => void;
    /**
     * Low-level helper: just fires the assign event without touching the
     * pending set. Caller is responsible for marking/clearing the user
     * — required when the assignment is preceded by an extra step (e.g.
     * promoting a viewer mailbox to editor) that must also block the UI.
     */
    dispatchAssignEvent: (
        user: AssignableUser,
        options?: { onSettled?: () => void },
    ) => void;
    unassignUser: (userId: string) => void;
};

/**
 * Centralizes assign / unassign mutations against the currently selected
 * thread. Wraps `useThreadsEventsCreate` and the post-mutation cache
 * invalidation, and tracks the set of in-flight user ids so the UI can
 * disable affordances and show per-row spinners — even when several
 * toggles are pending concurrently.
 */
export const useThreadAssignment = (): UseThreadAssignmentResult => {
    const {
        selectedThread,
        invalidateThreadEvents,
        invalidateThreadsList,
        invalidateThreadsStats,
        pinThreads,
    } = useMailboxContext();
    const assignedUsers = useAssignedUsers();
    const [mutatingUserIds, setMutatingUserIds] = useState<ReadonlySet<string>>(
        () => new Set(),
    );
    const markUserMutating = (id: string) => {
        setMutatingUserIds((prev) => {
            if (prev.has(id)) return prev;
            const next = new Set(prev);
            next.add(id);
            return next;
        });
    };
    const clearUserMutating = (id: string) => {
        setMutatingUserIds((prev) => {
            if (!prev.has(id)) return prev;
            const next = new Set(prev);
            next.delete(id);
            return next;
        });
    };
    const { mutate: createThreadEvent } = useThreadsEventsCreate();

    // Stable reference: consumers diff this Set across renders to detect
    // server-confirmed assignment changes, so it must only change when the
    // underlying assigned users actually change.
    const assignedUserIds = useMemo(
        () => new Set(assignedUsers.map((u) => u.id)),
        [assignedUsers],
    );

    // Single code path for assign + unassign: both fire the same
    // `ThreadEvent` shape (only the `type` and the assignee payload differ)
    // and share the exact same post-mutation invalidations.
    //
    // Pin the thread before invalidating the list so it survives a refetch
    // that would drop it under the active filter (e.g. unassign while
    // viewing "Assigned to me", or assign while viewing "Unassigned"). The
    // patcher mirrors the mutation on `assigned_users` so the cached row
    // reflects the new state until the thread events refetch confirms it.
    const fireAssignmentEvent = (
        type: typeof ThreadEventTypeEnum.assign | typeof ThreadEventTypeEnum.unassign,
        assignee: { id: string; name: string },
        options?: { onSettled?: () => void },
    ) => {
        if (!selectedThread?.id) {
            options?.onSettled?.();
            return;
        }
        const threadId = selectedThread.id;
        createThreadEvent(
            {
                threadId,
                data: {
                    type,
                    data: { assignees: [assignee] },
                },
            },
            {
                onSuccess: async () => {
                    pinThreads([threadId], (thread) => {
                        if (type === ThreadEventTypeEnum.assign) {
                            if (thread.assigned_users.some((u) => u.id === assignee.id)) {
                                return thread;
                            }
                            return {
                                ...thread,
                                assigned_users: [...thread.assigned_users, assignee],
                            };
                        }
                        return {
                            ...thread,
                            assigned_users: thread.assigned_users.filter(
                                (u) => u.id !== assignee.id,
                            ),
                        };
                    });
                    await invalidateThreadEvents();
                    await invalidateThreadsList();
                    await invalidateThreadsStats();
                },
                onSettled: () => options?.onSettled?.(),
            },
        );
    };

    const dispatchAssignEvent = (
        user: AssignableUser,
        options?: { onSettled?: () => void },
    ) => {
        fireAssignmentEvent(
            ThreadEventTypeEnum.assign,
            { id: user.id, name: user.full_name || user.email || "" },
            options,
        );
    };

    const assignUser = (user: AssignableUser) => {
        markUserMutating(user.id);
        dispatchAssignEvent(user, {
            onSettled: () => clearUserMutating(user.id),
        });
    };

    const unassignUser = (userId: string) => {
        const target = assignedUsers.find((u) => u.id === userId);
        if (!target) return;
        markUserMutating(userId);
        fireAssignmentEvent(
            ThreadEventTypeEnum.unassign,
            { id: target.id, name: target.name },
            { onSettled: () => clearUserMutating(userId) },
        );
    };

    return {
        assignedUserIds,
        mutatingUserIds,
        markUserMutating,
        clearUserMutating,
        assignUser,
        dispatchAssignEvent,
        unassignUser,
    };
};

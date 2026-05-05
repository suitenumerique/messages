import { useMemo } from "react";
import { ThreadEventTypeEnum } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";

export type AssignedUser = {
    id: string;
    name: string;
};

/**
 * Derive the current set of assigned users from the thread event history.
 *
 * Events arrive in chronological order (oldest first). We walk the list
 * in reverse and keep the most recent ASSIGN/UNASSIGN decision per user,
 * because assignment state is event-sourced (no denormalized field on Thread).
 */
export const useAssignedUsers = (): AssignedUser[] => {
    const { threadEvents } = useMailboxContext();

    return useMemo(() => {
        if (!threadEvents || threadEvents.length === 0) return [];

        const resolved = new Set<string>();
        const assigned: AssignedUser[] = [];

        for (let i = threadEvents.length - 1; i >= 0; i--) {
            const event = threadEvents[i];
            if (
                event.type !== ThreadEventTypeEnum.assign &&
                event.type !== ThreadEventTypeEnum.unassign
            ) {
                continue;
            }
            const data = event.data;
            if (!("assignees" in data)) continue;

            const assignees = data.assignees as ReadonlyArray<AssignedUser>;
            for (const assignee of assignees) {
                if (resolved.has(assignee.id)) continue;
                resolved.add(assignee.id);
                if (event.type === ThreadEventTypeEnum.assign) {
                    assigned.push(assignee);
                }
            }
        }

        return assigned;
    }, [threadEvents]);
};

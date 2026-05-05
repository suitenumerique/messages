import type { TFunction } from "i18next";
import { ThreadEvent as ThreadEventType, ThreadEventTypeEnum, ThreadEventAssigneesData } from "@/features/api/gen/models";

type Assignee = { id: string; name: string };

export type AssignmentEvent = ThreadEventType & {
    type: 'assign' | 'unassign';
    data: ThreadEventAssigneesData;
};

/**
 * Returns the localised sentence describing an ASSIGN/UNASSIGN event.
 *
 * Picks a wording variant based on who acts and who is impacted so the result
 * stays grammatically correct when the current user is involved — the previous
 * single-template approach produced sentences like "Vous a assigné Vous" once
 * self-substitution kicked in, and "User Un a assigné User Un" when a user
 * self-assigned.
 *
 * Cases (for each of assign/unassign):
 *   A.  author = me, assignees = [me]                        → "You assigned yourself"
 *   B.  author = me, assignees = [me, …others]               → "You assigned X and yourself"
 *   C.  author = me, assignees = [others]                    → "You assigned X"
 *   D.  author ≠ me, assignees = [me]                        → "X assigned you"
 *   E.  author ≠ me, assignees = [me, …others (no author)]   → "X assigned you and Y"
 *   F.  author ≠ me, in assignees alone                      → "X assigned themself"
 *   G.  author ≠ me, in assignees + others (no me)           → "X assigned themself and Y"
 *   H.  author ≠ me, in assignees + me                       → "X assigned you and themself"
 *   I.  author ≠ me, in assignees + me + others              → "X assigned you, themself and Y"
 *   J.  author ≠ me, no self / no author-self involved       → "X assigned Y" (legacy key)
 *   K.  author = null (system), assignees = [me]             → "You were unassigned"
 *   K2. author = null (system), assignees = [me, …others]    → "You and X were unassigned"
 *   L.  author = null (system), no self involved             → "X was unassigned" (legacy key)
 */
export const buildAssignmentMessage = (
    event: AssignmentEvent,
    currentUserId: string | undefined,
    t: TFunction,
): string => {
    const isAssign = event.type === ThreadEventTypeEnum.assign;
    const assignees: Assignee[] = event.data.assignees ?? [];
    const authorId = event.author?.id;
    const authorName = event.author?.full_name || event.author?.email || t("Unknown");
    const isAuthorSelf = !!currentUserId && authorId === currentUserId;
    const isSystem = event.author === null;

    const selfInAssignees = !!currentUserId && assignees.some((a) => a.id === currentUserId);
    // Author appears among assignees AND the viewer is not the author — that's
    // a "self-assign by a third party" from the viewer's perspective.
    const authorInAssignees = !isAuthorSelf && !!authorId && assignees.some((a) => a.id === authorId);

    // "Others" excludes both the viewer (shown as "you") and the author when
    // they appear in assignees (shown as "themself").
    const others = assignees.filter((a) => a.id !== currentUserId && a.id !== authorId);
    const othersNames = others.map((a) => a.name).join(", ");
    const othersCount = others.length;

    // System-emitted UNASSIGN (user lost edit rights, backend has no acting author).
    // The backend groups every user removed by a single access change in one
    // event, so `assignees` may contain the viewer alongside others — keep the
    // full list visible instead of collapsing it to "You were unassigned".
    if (isSystem && !isAssign) {
        if (selfInAssignees && othersCount === 0) {
            return t("You were unassigned");
        }
        if (selfInAssignees) {
            return t("You and {{assignees}} were unassigned", {
                assignees: othersNames,
                count: othersCount,
            });
        }
        return t("{{assignees}} was unassigned", {
            assignees: assignees.map((a) => a.name).join(", "),
            count: assignees.length,
        });
    }

    // A/B/C — acting user is the viewer
    if (isAuthorSelf) {
        if (selfInAssignees && othersCount === 0) {
            return isAssign ? t("You assigned yourself") : t("You unassigned yourself");
        }
        if (selfInAssignees) {
            return isAssign
                ? t("You assigned {{assignees}} and yourself", { assignees: othersNames, count: othersCount })
                : t("You unassigned {{assignees}} and yourself", { assignees: othersNames, count: othersCount });
        }
        return isAssign
            ? t("You assigned {{assignees}}", { assignees: othersNames, count: othersCount })
            : t("You unassigned {{assignees}}", { assignees: othersNames, count: othersCount });
    }

    // Author is a third party from here on.

    // H/I — viewer is in assignees AND author self-assigned
    if (selfInAssignees && authorInAssignees) {
        if (othersCount === 0) {
            return isAssign
                ? t("{{author}} assigned you and themself", { author: authorName })
                : t("{{author}} unassigned you and themself", { author: authorName });
        }
        return isAssign
            ? t("{{author}} assigned you, themself and {{assignees}}", { author: authorName, assignees: othersNames, count: othersCount })
            : t("{{author}} unassigned you, themself and {{assignees}}", { author: authorName, assignees: othersNames, count: othersCount });
    }

    // D/E — viewer is in assignees, author did not self-assign
    if (selfInAssignees) {
        if (othersCount === 0) {
            return isAssign
                ? t("{{author}} assigned you", { author: authorName })
                : t("{{author}} unassigned you", { author: authorName });
        }
        return isAssign
            ? t("{{author}} assigned you and {{assignees}}", { author: authorName, assignees: othersNames, count: othersCount })
            : t("{{author}} unassigned you and {{assignees}}", { author: authorName, assignees: othersNames, count: othersCount });
    }

    // F/G — author self-assigned (viewer not involved)
    if (authorInAssignees) {
        if (othersCount === 0) {
            return isAssign
                ? t("{{author}} assigned themself", { author: authorName })
                : t("{{author}} unassigned themself", { author: authorName });
        }
        return isAssign
            ? t("{{author}} assigned themself and {{assignees}}", { author: authorName, assignees: othersNames, count: othersCount })
            : t("{{author}} unassigned themself and {{assignees}}", { author: authorName, assignees: othersNames, count: othersCount });
    }

    // J — nobody special, legacy 3rd-person wording
    const assigneesNames = assignees.map((a) => a.name).join(", ");
    return isAssign
        ? t("{{author}} assigned {{assignees}}", { author: authorName, assignees: assigneesNames, count: assignees.length })
        : t("{{author}} unassigned {{assignees}}", { author: authorName, assignees: assigneesNames, count: assignees.length });
};

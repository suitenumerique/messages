import { Button } from "@gouvfr-lasuite/cunningham-react";
import { UserRow } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";

type AssignedUsersSectionProps = {
    assignedUsers: ReadonlyArray<{ id: string; name: string; email?: string }>;
    canUpdate: boolean;
    mutatingUserIds: ReadonlySet<string>;
    onUnassign: (userId: string) => void;
};

/**
 * Rendered through the ShareModal `children` slot, above the members list.
 * Title is dynamic ("Assigné à N personne") so the user instantly sees how
 * many people are in charge of this thread.
 *
 * Rows here deliberately omit the `__row--assigned` modifier (and thus the
 * person badge on the avatar): every user listed in this section is
 * assigned by definition, so the section heading already carries that
 * information — repeating it on each avatar would be noise.
 */
export const AssignedUsersSection = ({ assignedUsers, canUpdate, mutatingUserIds, onUnassign }: AssignedUsersSectionProps) => {
    const { t } = useTranslation();

    if (assignedUsers.length === 0) return null;

    return (
        <section className="share-modal-extensions__assigned">
            <h3 className="share-modal-extensions__assigned__title">
                {t('Assigned to {{count}} people', {
                    count: assignedUsers.length,
                    defaultValue_one: 'Assigned to {{count}} person',
                })}
            </h3>
            <ul className="share-modal-extensions__assigned__list">
                {assignedUsers.map((user) => (
                    <li
                        key={user.id}
                        className="share-modal-extensions__row"
                    >
                        <UserRow
                            fullName={user.name}
                            email={user.email ?? ""}
                            showEmail
                        />
                        {canUpdate && (
                            <Button
                                size="small"
                                color="error"
                                variant="tertiary"
                                disabled={mutatingUserIds.size > 0}
                                onClick={() => onUnassign(user.id)}
                            >
                                {t('Unassign')}
                            </Button>
                        )}
                    </li>
                ))}
            </ul>
        </section>
    );
};

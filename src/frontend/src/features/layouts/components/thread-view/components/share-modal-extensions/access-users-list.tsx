import { Spinner, UserRow } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import type { ThreadAccessDetail, UserWithoutAbilities } from "@/features/api/gen";

type AccessWithUsers = ThreadAccessDetail & {
    users: readonly UserWithoutAbilities[];
};

type AccessUsersListProps = {
    access: AccessWithUsers;
    assignedUserIds: ReadonlySet<string>;
    canAssign: boolean;
    mutatingUserIds: ReadonlySet<string>;
    onAssign: (user: UserWithoutAbilities, access: AccessWithUsers) => void;
    onUnassign: (userId: string) => void;
};

/**
 * Renders the users of a mailbox below its row, but only when listing them
 * adds information beyond what the mailbox row already shows. A
 * single-user identity mailbox is essentially a synonym for its user, so
 * we skip the sub-list there and let the parent widget render the toggle
 * inline next to the role select.
 *
 * Each row reuses the upstream `<UserRow>` (avatar + name + email) so the
 * layout matches the mailbox rows exactly. Assigned users get a brand-color
 * "person" badge in the top-right of their avatar (rendered via CSS
 * pseudo-element on `.c__avatar`); the explicit "Assign / Unassign"
 * button next to it carries the actual toggle.
 */
export const AccessUsersList = ({
    access,
    assignedUserIds,
    canAssign,
    mutatingUserIds,
    onAssign,
    onUnassign,
}: AccessUsersListProps) => {
    const { t } = useTranslation();
    if (!access.users || access.users.length === 0) return null;

    const isSingleIdentityUser =
        access.users.length === 1 && access.mailbox.is_identity !== false;
    if (isSingleIdentityUser) return null;

    return (
        <ul className="share-modal-extensions__users">
            {access.users.map((user) => {
                const isAssigned = assignedUserIds.has(user.id);
                const isMutating = mutatingUserIds.has(user.id);
                return (
                    <li
                        key={user.id}
                        className={clsx("share-modal-extensions__row", {
                            "share-modal-extensions__row--assigned": isAssigned,
                        })}
                        aria-label={isAssigned ? t('Assigned to this thread') : undefined}
                    >
                        <UserRow
                            fullName={user.full_name ?? ""}
                            email={user.email ?? ""}
                            showEmail
                        />
                        {canAssign && (isAssigned ? (
                            <Button
                                onClick={() => onUnassign(user.id)}
                                size="small"
                                color="error"
                                variant="tertiary"
                                className="share-modal-extensions__toggle"
                                disabled={mutatingUserIds.size > 0}
                            >
                                {t('Unassign')}
                            </Button>
                        ) : (
                            <Button
                                onClick={() => onAssign(user, access)}
                                size="small"
                                variant="secondary"
                                className="share-modal-extensions__toggle"
                                disabled={mutatingUserIds.size > 0}
                                icon={isMutating ? <Spinner size="sm" /> : undefined}
                            >
                                {t('Assign')}
                            </Button>
                        ))}
                    </li>
                );
            })}
        </ul>
    );
};

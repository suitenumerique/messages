// FORK: reproduced from @gouvfr-lasuite/ui-kit InvitationUserSelectorList
// since the upstream component is not publicly exported. Preserves the
// upstream CSS classes (c__add-share-user-list, c__add-share-user-item)
// so the visual styling bundled in ui-kit's style.css applies as-is.
import { ReactNode, useState } from "react";
import { Button, useCunningham } from "@gouvfr-lasuite/cunningham-react";
import type { DropdownMenuOption, UserData } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { AccessRoleDropdown } from "./access-role-dropdown";

export type InvitationUserSelectorListProps<UserType> = {
    users: UserData<UserType>[];
    onRemoveUser: (user: UserData<UserType>) => void;
    rightActions?: ReactNode;
    onShare: () => void;
    roles: DropdownMenuOption[];
    selectedRole: string;
    shareButtonLabel?: string;
    onSelectRole: (role: string) => void;
};

export const InvitationUserSelectorList = <UserType,>({
    users,
    onRemoveUser,
    rightActions,
    onShare,
    shareButtonLabel,
    roles,
    selectedRole,
    onSelectRole,
}: InvitationUserSelectorListProps<UserType>) => {
    // Aliased to `tc` so the i18next-cli parser does not extract Cunningham's
    // own translation keys (e.g. `components.share.*`) into our locale files.
    const { t: tc } = useCunningham();
    const [isOpen, setIsOpen] = useState(false);
    return (
        <div className="c__add-share-user-list" data-testid="selected-users-list">
            <div className="c__add-share-user-list__items">
                {users.map((user) => (
                    <InvitationUserSelectorItem
                        key={user.id}
                        user={user}
                        onRemoveUser={onRemoveUser}
                    />
                ))}
            </div>
            <div className="c__add-share-user-list__right-actions">
                {rightActions}
                <AccessRoleDropdown
                    roles={roles}
                    selectedRole={selectedRole}
                    onSelect={onSelectRole}
                    isOpen={isOpen}
                    onOpenChange={setIsOpen}
                    canDelete={false}
                    onDelete={undefined}
                />
                <Button onClick={onShare}>
                    {shareButtonLabel ?? tc("components.share.shareButton")}
                </Button>
            </div>
        </div>
    );
};

type InvitationUserSelectorItemProps<UserType> = {
    user: UserData<UserType>;
    onRemoveUser: (user: UserData<UserType>) => void;
};

const InvitationUserSelectorItem = <UserType,>({
    user,
    onRemoveUser,
}: InvitationUserSelectorItemProps<UserType>) => {
    const { t } = useTranslation();
    const displayName = user.full_name || user.email;
    return (
        <div className="c__add-share-user-item" data-testid="selected-user-item">
            <span>{displayName}</span>
            <Button
                aria-label={t("Remove {{displayName}}", { displayName })}
                variant="tertiary"
                color="neutral"
                size="nano"
                onClick={() => onRemoveUser?.(user)}
                icon={<span className="material-icons">close</span>}
            />
        </div>
    );
};

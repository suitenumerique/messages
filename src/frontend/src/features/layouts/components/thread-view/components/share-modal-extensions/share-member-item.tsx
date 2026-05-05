// FORK: reproduced from @gouvfr-lasuite/ui-kit ShareMemberItem so we can
// inject a `rightExtras` slot (e.g. an inline "Assign" CTA) next to the
// role dropdown. CSS classes (`c__share-member-item`, `c__share-member-item__right`)
// are preserved so ui-kit's bundled styles apply as-is.
import { ReactNode, useState } from "react";
import {
    type AccessData,
    QuickSearchItemTemplate,
    UserRow,
    type DropdownMenuOption,
    type DropdownMenuProps,
} from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { AccessRoleDropdown } from "./access-role-dropdown";

export type ShareMemberItemProps<UserType, AccessType> = {
    accessData: AccessData<UserType, AccessType>;
    roles: DropdownMenuOption[];
    updateRole?: (access: AccessData<UserType, AccessType>, role: string) => void;
    deleteAccess?: (access: AccessData<UserType, AccessType>) => void;
    canUpdate?: boolean;
    roleTopMessage?: DropdownMenuProps["topMessage"];
    accessRoleKey?: keyof AccessData<UserType, AccessType>;
    rightExtras?: ReactNode;
    /**
     * Optional extra class on the row wrapper. Used by consumers to flag
     * row-level state (e.g. assignment) that the upstream component does
     * not know about — CSS hooks into it to decorate the avatar.
     */
    wrapperClassName?: string;
};

export const ShareMemberItem = <UserType, AccessType>({
    accessData,
    accessRoleKey = "role",
    roles,
    updateRole,
    deleteAccess,
    canUpdate = true,
    roleTopMessage,
    rightExtras,
    wrapperClassName,
}: ShareMemberItemProps<UserType, AccessType>) => {
    const [isRoleOpen, setIsRoleOpen] = useState(false);
    const accessFlags = accessData as { is_explicit?: boolean; can_delete?: boolean };
    const canDelete =
        Boolean(deleteAccess) &&
        accessFlags.is_explicit !== false &&
        accessFlags.can_delete !== false;
    return (
        <div className={clsx("c__share-member-item", wrapperClassName)}>
            <QuickSearchItemTemplate
                testId="share-member-item"
                left={
                    <UserRow
                        fullName={accessData.user.full_name}
                        email={accessData.user.email}
                        showEmail
                    />
                }
                alwaysShowRight={true}
                right={
                    <div className="c__share-member-item__right">
                        {rightExtras}
                        <AccessRoleDropdown
                            roles={roles}
                            selectedRole={accessData[accessRoleKey] as string}
                            onSelect={(role) => updateRole?.(accessData, role)}
                            isOpen={isRoleOpen}
                            onOpenChange={setIsRoleOpen}
                            canUpdate={canUpdate}
                            roleTopMessage={roleTopMessage}
                            canDelete={canDelete}
                            onDelete={deleteAccess ? () => deleteAccess(accessData) : undefined}
                        />
                    </div>
                }
            />
        </div>
    );
};
